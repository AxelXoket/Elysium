"""routers/tts_runtime.py -- Voice that actually runs (Phase V3).

Shares the `/tts` prefix with routers/tts.py, which stays the pure host half
(discovery, settings, readiness - never spawns anything). Everything that owns
a process lives here.

    GET    /tts/state                              what is loaded, and what is wrong
    POST   /tts/load                               load the selected model
    POST   /tts/unload                             give the VRAM back
    POST   /tts/speak                              synthesize one passage
    GET    /tts/audio/{audio_id}                   fetch the result

    GET    /tts/runtimes/{engine}/plan             what setup will do, and how big
    POST   /tts/runtimes/{engine}/install          start it (returns immediately)
    GET    /tts/runtimes/{engine}/install          watch it
    POST   /tts/runtimes/{engine}/install/cancel   stop it
    DELETE /tts/runtimes/{engine}                  remove it, reclaim the disk

    GET    /tts/voices                             reference voices (clone sources)
    POST   /tts/voices/{voice_id}                  upload a clip (+label/transcript)
    POST   /tts/voices/{voice_id}/transcript       edit the words by hand
    POST   /tts/voices/{voice_id}/transcribe       let the loaded engine draft them
    DELETE /tts/voices/{voice_id}                  remove a voice

Privacy notes specific to this file:
  - Generated audio is written to a cache directory and is the user's speech in
    audible form, so it does not outlive the session: locking the vault or
    unloading wipes it. Nothing about it is uploaded anywhere.
  - Installing an engine is the one action in Elysium that contacts anywhere
    other than OpenRouter. It only ever downloads packages, only when the user
    explicitly starts it, and sends nothing.
"""

import json
import logging
import re
import time
from functools import partial
from pathlib import Path
from typing import NamedTuple

import anyio.to_thread
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import config
import secure_delete
import speech_prep
from database import get_setting, set_setting
from vault_state import VaultLockedError
from tts import provision, refs, scan_roots, speed, stream_hook
from tts import host as tts_host
from tts.errors import (
    TTS_LOAD_TIMEOUT,
    TTS_MODEL_UNKNOWN,
    TTS_OUT_OF_MEMORY,
    TTS_REFERENCE_CLIP_STUCK,
    TTS_REFERENCE_INVALID,
    TTS_REFERENCE_TOO_SHORT,
    TTS_RUNTIME_INSTALL_FAILED,
    TTS_SYNTHESIS_FAILED,
    TTS_AUDIO_EXPIRED,
    TTS_NOTHING_TO_SPEAK,
    TTS_TRANSCRIBE_UNSUPPORTED,
    TTS_TRANSCRIPT_REQUIRED,
    TTS_WORKER_CRASHED,
    TTS_WORKER_FAILED,
    TTS_WORKER_UNAVAILABLE,
    TtsError,
)
from tts.registry import adapter_for, all_adapters
from tts.worker_client import WorkerFailure

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tts", tags=["tts"])

SETTING_ACTIVE_UID = "tts_active_uid"
_AUDIO_ID = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_MAX_TEXT_CHARS = 5000

# Status per code, EXPLICIT and matching docs/frontend_contract.md - the old
# suffix heuristic ("endswith 'failed'") quietly disagreed with the contract
# for five codes, including sending a documented 504 as a 409.
_STATUS = {
    TTS_LOAD_TIMEOUT: 504,
    TTS_OUT_OF_MEMORY: 500,
    TTS_WORKER_CRASHED: 500,
    TTS_WORKER_FAILED: 500,
    TTS_WORKER_UNAVAILABLE: 500,
    TTS_SYNTHESIS_FAILED: 500,
    TTS_RUNTIME_INSTALL_FAILED: 500,
    TTS_REFERENCE_INVALID: 400,
    TTS_REFERENCE_TOO_SHORT: 400,
    # Not 400: the clip that was just sent is fine and nothing about the
    # request needs changing. Something on this machine is holding the old
    # file, so the honest answer is "conflict, try again in a moment".
    TTS_REFERENCE_CLIP_STUCK: 409,
    TTS_TRANSCRIPT_REQUIRED: 400,
    TTS_TRANSCRIBE_UNSUPPORTED: 409,
    TTS_NOTHING_TO_SPEAK: 400,
    TTS_AUDIO_EXPIRED: 404,
}


def _host():
    return tts_host.get_host()


def _resolve(uid: str | None):
    from_setting = not uid
    uid = uid or get_setting(SETTING_ACTIVE_UID)
    if not uid:
        raise HTTPException(400, TTS_MODEL_UNKNOWN)
    models = scan_roots().models
    for model in models:
        if model.uid == uid:
            return model
    # Uids gained their scan root (KÖK 15: two roots could hold a folder of the
    # same name and collide onto one id). A selection saved under the old
    # scheme is recognised here and rewritten ONCE, so the migration costs the
    # user nothing - the alternative was their chosen voice silently becoming
    # "no model selected" on the first launch after the change.
    for model in models:
        if model.legacy_uid and model.legacy_uid == uid:
            if from_setting:
                set_setting(SETTING_ACTIVE_UID, model.uid)
                logger.info("tts: migrated the selected model to its new id.")
            return model
    raise HTTPException(400, TTS_MODEL_UNKNOWN)


def a_voice_model_is_selected() -> bool:
    """Is there a voice model to speak WITH, right now?

    Deliberately a settings read and nothing else - no scan, no readiness
    evaluation, no VRAM probe. It is called on the hot path of every stream, and
    the expensive answer belongs to /tts/active.

    Used to decide whether a stream arms a dormant speaker. That used to be
    gated on the sticky `tts_voice_ever_enabled` flag while SpeakLiveButton
    renders on model readiness alone, so a user who installed an engine, picked
    a model and a reference voice and never touched the "Voice replies" toggle
    (whose own description says "Off - chat stays text-only") got
    404 tts_nothing_streaming from the Speak icon on a reply that was very much
    still streaming. A button that can only produce an error toast is a broken
    promise.
    """
    try:
        return bool((get_setting(SETTING_ACTIVE_UID) or "").strip())
    except Exception:                                    # noqa: BLE001
        return False


def _values_for(model) -> dict:
    from routers.tts import _effective

    try:
        return _effective(model)[0]
    except (VaultLockedError, HTTPException):
        # A locked vault must reach the 423 handler and a contract error must
        # reach the client - degrading either to "empty settings" would run
        # the engine on silent defaults while pretending nothing happened.
        raise
    except Exception:                           # noqa: BLE001
        return {}


def _fail(exc: WorkerFailure):
    """Worker failures already carry a code the frontend knows; the status just
    has to be the one the contract documents.

    Reads `.reason` rather than `.detail`. They are the same string - see
    WorkerFailure - but only one of them says at the log line what it is. This
    used to log `exc.detail[:200]`, which for any failure the WORKER reported
    was the worker's own error text, and an engine builds the sentence it was
    asked to speak into its exception. That put model replies into
    elysium.log, plaintext and outside the vault. The truncation to 200 was
    never the guard it looked like; 200 characters of a reply is a reply.
    """
    logger.warning("tts: %s (%s)", exc.code, exc.reason)
    raise HTTPException(_STATUS.get(exc.code, 409), exc.code)


# ── the loaded model ─────────────────────────────────────────────────────────

class LoadBody(BaseModel):
    uid: str | None = None


@router.get("/state")
def voice_state() -> dict:
    # poll_health is called here rather than only on a timer: this endpoint is
    # what the UI polls, so noticing a dead worker exactly when someone is
    # looking is the point.
    return _host().poll_health()


@router.post("/load")
def voice_load(body: LoadBody) -> dict:
    model = _resolve(body.uid)
    try:
        return _host().load(model, _values_for(model))
    except WorkerFailure as exc:
        _fail(exc)


@router.post("/unload")
def voice_unload() -> dict:
    return _host().unload("asked to")


class VoiceModeBody(BaseModel):
    enabled: bool


@router.get("/voice-mode")
def get_voice_mode() -> dict:
    """The global voice toggle - what turns the delivery-tag prompt on.

    `prompt_chars` is exposed so the frontend context gauge can charge the
    injected block to fixed cost exactly the way the backend budget does (G2);
    `active` says whether the prompt would ACTUALLY inject right now (toggle on
    AND a tag-capable engine selected)."""
    import voice_tags

    enabled = (get_setting(voice_tags.SETTING_VOICE_ENABLED) or "").strip() in ("1", "true")
    return {
        "enabled": enabled,
        "active": bool(voice_tags.voice_block()),
        "prompt_chars": voice_tags.VOICE_PROMPT_CHARS,
    }


@router.post("/voice-mode")
def set_voice_mode(body: VoiceModeBody) -> dict:
    import voice_tags
    from database import get_db

    with get_db() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (voice_tags.SETTING_VOICE_ENABLED, "1" if body.enabled else "0"),
        )
        if body.enabled:
            # Sticky, never cleared: rows written while voice was on still
            # need their tags hidden after it is turned off (audit-2).
            con.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (voice_tags.SETTING_VOICE_EVER, "1"),
            )
    if body.enabled:
        voice_tags.mark_voice_ever_enabled()
    return get_voice_mode()


class SpeakBody(BaseModel):
    text: str | None = None
    message_id: int | None = None
    uid: str | None = None


def stored_pronunciations() -> dict[str, str]:
    """The user's reading rules, or {}.

    A FUNCTION, not a value, and that is deliberate: the live path hands this
    to open_speaker, which does not touch it until somebody actually speaks.
    Reading the vault when a stream merely STARTS would put a DB call back on
    the event loop that KÖK 8 just took off it, for a feature most replies
    never use.
    """
    import voice_tags

    try:
        raw = get_setting(voice_tags.SETTING_PRONUNCIATIONS)
    except Exception:                                    # noqa: BLE001
        return {}
    if not raw:
        return {}
    try:
        return voice_tags.sanitize_pronunciations(json.loads(raw))
    except (ValueError, TypeError):
        # Sanitised again on the way OUT as well as in: a row hand-edited (or
        # left by an older build) must not be able to inject a bracket into
        # every sentence of every reply.
        logger.warning("tts: stored pronunciations are not readable; ignoring.")
        return {}


def _stored_gap() -> float:
    """Silence between sentences, in seconds.

    The mechanism has existed and been tested all along - ChunkScheduler's
    `gapSeconds`, with its own test - and all three production callers built
    the player without options, so the value was always 0 and the dial the
    decision promised did not exist anywhere.
    """
    import voice_tags

    try:
        raw = get_setting(voice_tags.SETTING_SENTENCE_GAP)
        value = float(raw) if raw else voice_tags.GAP_DEFAULT
    except (TypeError, ValueError, Exception):           # noqa: BLE001
        return voice_tags.GAP_DEFAULT
    return max(voice_tags.GAP_MIN, min(voice_tags.GAP_MAX, value))


def _engine_transcribes(uid: str | None) -> bool:
    """Can the engine behind this model hear a clip and draft its words?

    Answered from the adapter's declared capabilities rather than by asking
    the worker: the worker's refusal arrives as a generic failure, and a
    generic failure is exactly what made this look like a broken engine.
    """
    if not uid:
        return False
    for model in scan_roots().models:
        if model.uid == uid:
            adapter = adapter_for(model.engine_id)
            return bool(adapter and adapter.capabilities.transcribes_reference)
    return False


def _narrative_pref() -> str:
    """How narration is voiced on the REPLAY path.

    The live stream carries the mode on its request; a replay has no request to
    carry it, so it reads the stored value instead. `same` is the shared
    default, so an unset preference behaves identically on both paths.
    """
    import voice_tags

    try:
        value = (get_setting(voice_tags.SETTING_NARRATIVE) or "same").strip()
    except Exception:                                    # noqa: BLE001
        return "same"
    return value if value in ("same", "narrator", "skip") else "same"


def _tag_prefs() -> tuple[int, str]:
    """(density cap, standing tone) from the vault, with safe fallbacks.

    Read ONCE per utterance rather than per sentence: a preference that changed
    halfway through a reply would give one paragraph a different delivery from
    the next, which reads as a glitch rather than as a setting.

    Every failure path returns the defaults. These are two comfort dials; a
    malformed value in the settings table must not be able to cost somebody
    their audio.
    """
    import voice_tags

    try:
        raw = get_setting(voice_tags.SETTING_TAG_DENSITY)
        density = (voice_tags.MAX_TAGS_PER_REPLY if raw in (None, "")
                   else max(voice_tags.TAG_DENSITY_MIN,
                            min(voice_tags.TAG_DENSITY_MAX, int(raw))))
    except (TypeError, ValueError):
        density = voice_tags.MAX_TAGS_PER_REPLY
    except Exception:                                    # noqa: BLE001
        return voice_tags.MAX_TAGS_PER_REPLY, ""
    try:
        tone = voice_tags.sanitize_tone(
            get_setting(voice_tags.SETTING_DEFAULT_TONE) or "")
    except Exception:                                    # noqa: BLE001
        tone = ""
    return density, tone


def _expand_reference(model, values: dict) -> tuple[dict, dict]:
    """Turn a saved voice id into what the worker actually needs.

    The settings UI stores `reference_voice` as a voice id from the refs
    library; the workers expect a FILE PATH (and Fish, the words spoken in it).
    An id nothing resolves is refused here with the reference code - letting it
    through would end as "clip not found" from deep inside an engine.
    """
    raw = str(values.get("reference_voice") or "").strip()
    if not raw:
        return values, {}
    if Path(raw).is_file() or Path(raw).is_dir():
        return values, {}                       # already a real path
    voice = refs.describe(raw)                  # RefError -> handled by caller
    adapter = adapter_for(model.engine_id)
    needs = bool(adapter and adapter.capabilities.needs_reference_transcript)
    refs.require_transcript(voice, needs)
    expanded = dict(values)
    expanded["reference_voice"] = str(Path(voice.path) / voice.audio_name)
    extra = {}
    if voice.transcript:
        extra["reference_transcript"] = voice.transcript
    return expanded, extra


def _stored_rate() -> float | None:
    """The reading-speed dial, or None when it was never set.

    tts/matrix.py advertises `speed` as APP_LEVEL - "applied by Elysium and
    works the same on every voice model" - and removes the engine's own rate
    knob from the settings panel on that promise. Nothing read or wrote
    SETTING_SPEED, so the promise was empty: the panel pointed at a Delivery
    control that did not exist, and the live path passed rate=None into
    speed.engine_values, whose clamp turns None into 1.0 and merged THAT over
    the model's saved value - while /speak still used the saved one. The same
    reply was paced differently live and on replay.

    Every failure path returns None (= the default pace). A malformed value in
    the settings table must not cost somebody their audio.
    """
    import voice_tags

    try:
        raw = get_setting(voice_tags.SETTING_SPEED)
    except Exception:                                    # noqa: BLE001
        return None
    if raw is None or not str(raw).strip():
        return None
    try:
        return speed.clamp(float(raw))
    except (TypeError, ValueError):
        return None


def _speak_source(body: SpeakBody) -> str:
    """The text to speak: given directly, or the RAW stored content of a
    message - which is the point of storing raw. The frontend only ever holds
    the stripped view, so per-message speak must come back here for the tags."""
    if body.message_id is not None:
        from database import get_db

        with get_db() as con:
            row = con.execute(
                "SELECT content FROM messages WHERE id = ?",
                (body.message_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "message_not_found")
        return (row["content"] or "").strip()
    return (body.text or "").strip()


class PreparedSpeech(NamedTuple):
    """The text an engine should be handed, and what was lost getting there.

    A bare `str` could not carry the second half, which is why the truncation
    only ever reached a log line.
    """
    text: str
    truncated: bool


def _prepare_speech_text(body: SpeakBody) -> PreparedSpeech:
    """Turn a speak request into the exact text an engine should be handed.

    Shared by BOTH replay endpoints on purpose. Every rule below was added
    because the two paths had drifted apart and the difference was audible -
    markdown read aloud, URLs spelled out, code fences recited, the density and
    standing-tone dials applying on one path and not the other. Same text in,
    same sound out is the whole contract; a second copy of this is a second
    voice waiting to happen.

    """
    import voice_tags

    text = _speak_source(body)
    if not text:
        raise HTTPException(400, TTS_NOTHING_TO_SPEAK)
    truncated = False
    if len(text) > _MAX_TEXT_CHARS:
        # Cut, but SAY so - a 200 that quietly dropped the tail of the reply
        # would leave the user blaming the model for stopping mid-sentence.
        # "SAY so" was a logger.warning and nothing else: /speak carried a
        # `truncated` field, /speak_stream had none, and the Speak button only
        # ever calls /speak_stream. So the one path a user can actually reach
        # cut the reply and said nothing at all.
        logger.warning("tts: speak text truncated to %d chars", _MAX_TEXT_CHARS)
        text = text[:_MAX_TEXT_CHARS]
        truncated = True
    model = _resolve(body.uid)
    adapter = adapter_for(model.engine_id)
    # V4: tag-capable engines keep well-formed [tags] (capped, deduped,
    # malformed spans dropped so they are never READ ALOUD); the others get
    # fully stripped text - they would speak the brackets.
    supports_tags = bool(adapter and adapter.capabilities.inline_prosody_tags)
    text = speech_prep.prepare(text, speech_prep.PrepOptions(
        engine_supports_tags=supports_tags,
        narrative=_narrative_pref(),
        # The user's standing tone closes its own narration; see _closing_tag.
        # Read here rather than defaulted, so the Speak button and the live
        # stream perform the clause after a `*...*` span the same way.
        speech_tag=_closing_tag(_tag_prefs()[1]),
        # The replay path preps the whole message itself, so the rules are
        # resolved here rather than handed to a queue. Same table either way -
        # a name has to be said the same whether the reply is arriving or
        # being repeated.
        pronunciations=stored_pronunciations(),
    ))
    if not text.strip():
        raise HTTPException(400, TTS_NOTHING_TO_SPEAK)
    # Tag capping and the standing tone are NOT applied here any more (KÖK 6).
    # Both belong per SENTENCE, next to the engine call, because that is the
    # unit both paths actually synthesise:
    #   - the tone was prefixed once to the whole message and the message was
    #     then split, so every sentence after the first was spoken without it;
    #   - and /speak_stream sanitised here AND again inside its own synth,
    #     applying the density cap twice to the same words.
    # _speak_in_sentences and make_stream_synth now each do it once, sharing
    # one TagBudget across the reply.
    return PreparedSpeech(text, truncated)


@router.post("/speak")
def voice_speak(body: SpeakBody) -> dict:
    """Speak a stored message and return ONE joined wav.

    Kept for the callers that genuinely want a single file. Anything that plays
    a reply to a person should use `/speak_stream` instead - this shape cannot
    make a sound until the last sentence is finished.
    """
    # One source of truth now: the preparer decides, both endpoints report.
    # This route used to recompute it from _speak_source and /speak_stream did
    # not compute it at all.
    text, truncated = _prepare_speech_text(body)
    model = _resolve(body.uid)
    adapter = adapter_for(model.engine_id)
    host = _host()
    try:
        values, extra = _expand_reference(model, _values_for(model))
        # The SAME speed plan the live path builds. Replay used to ignore the
        # dial entirely (no native merge, no DSP frame), so a reply spoken at
        # 0.85 live came back at 1.0 when the Speak button repeated it.
        specs = adapter.describe_settings(model) if adapter else []
        rate = _stored_rate()
        values = {**values, **speed.engine_values(specs, rate)}
        dsp_rate = speed.plan(specs, rate).dsp_rate
        if not _dsp_noop(dsp_rate):
            extra = {**(extra or {}), "rate": dsp_rate}
        snap = host.snapshot()
        if snap["state"] != "loaded" or snap["uid"] != model.uid:
            host.load(model, values)
        density, tone = _tag_prefs()
        result = _speak_in_sentences(
            host, text, values, extra,
            supports_tags=bool(adapter and adapter.capabilities.inline_prosody_tags),
            density=density, tone=tone, message_id=body.message_id,
        )
    except refs.RefError as exc:
        raise HTTPException(_STATUS.get(exc.code, 400), exc.code)
    except WorkerFailure as exc:
        _fail(exc)
    path = Path(result.get("path", ""))
    return {
        "audio_id": path.stem,
        "sample_rate": result.get("sample_rate"),
        "seconds": result.get("seconds"),
        "truncated": truncated,
    }


_STREAM_SSE_HEADERS = {
    "Cache-Control": "no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

#: How long the drain loop parks with nothing to send. Short enough that a
#: finished chunk leaves promptly, long enough that an idle utterance is not a
#: spin. Mirrors StreamSpeaker's own idle wait.
_DRAIN_TICK_S = 0.05


@router.post("/speak_stream")
async def voice_speak_stream(body: SpeakBody) -> StreamingResponse:
    """Speak a stored message, sending each piece AS IT IS MADE.

    WHY THIS EXISTS
        `/speak` synthesises every sentence, joins them into one wav and only
        then answers. On a four-paragraph reply that is the whole utterance -
        twenty seconds or more - of complete silence before a single sound. No
        amount of engine speed fixes it: the shape of the endpoint is the
        latency. The live path has always streamed; this is the Speak button
        catching up, and the two now differ only in where the text comes from.

    The wire format is deliberately the SAME `voice_chunk` / `voice_error` /
    `voice_done` the streaming completion emits, so the client feeds both to
    one player instead of keeping two ways to hear a sentence.
    """
    import anyio

    requested_at = time.perf_counter()
    # Recorded BEFORE anything runs, because the two ways this endpoint can be
    # slow are indistinguishable from the outside: a press that lands on a ready
    # engine waits for synthesis, and a press that lands while the model is
    # still loading waits for the load first and then for synthesis. Both are
    # silence. The log carried neither, so "why did that take twelve seconds"
    # could not be answered from a log file at all - only guessed at, and a
    # guess about where time goes is how the last two wrong diagnoses started.
    try:
        engine_was_ready = _host().snapshot().get("state") == "loaded"
    except Exception:                                    # noqa: BLE001
        # Diagnostics must never be the reason a reply is not spoken.
        engine_was_ready = False

    def _prepare():
        """Both halves of the set-up, in ONE worker-thread hop.

        Neither belongs on the event loop. _prepare_speech_text reads the
        message out of the vault DB; make_stream_synth resolves the model,
        walks the models folder and reads the stored settings - hundreds of
        milliseconds on a cold cache, and every OTHER live stream is frozen for
        the duration because none of them can be resumed until this coroutine
        yields. One hop rather than two: they are always needed together and a
        second await point buys nothing.
        """
        return (_prepare_speech_text(body),
                make_stream_synth(body.uid,
                                  message_id=body.message_id),
                _narrative_pref(), stored_pronunciations())

    try:
        (text, truncated), synth, narrative, reading_rules = (
            await anyio.to_thread.run_sync(_prepare)
        )
    except refs.RefError as exc:
        raise HTTPException(_STATUS.get(exc.code, 400), exc.code)
    except WorkerFailure as exc:
        _fail(exc)
    except HTTPException:
        raise                       # already carries its own contract code
    except Exception:                                    # noqa: BLE001
        # Anything else escaped as a bare 500 with no `detail` at all, so the
        # frontend had no code to map and showed the generic "Something went
        # wrong" - about the voice engine, which is precisely the sentence the
        # tts_* vocabulary exists to replace. Found by the first test ever
        # written for this endpoint's failure contract (KÖK 13).
        logger.warning("tts: speak_stream setup failed", exc_info=True)
        raise HTTPException(_STATUS[TTS_WORKER_FAILED], TTS_WORKER_FAILED)
    prepared_at = time.perf_counter()

    async def event_source():
        speaker = stream_hook.StreamSpeaker(
            synth,
            engine_supports_tags=bool(getattr(synth, "engine_supports_tags", False)),
            narrative=narrative,
            pronunciations=reading_rules,
        )
        index = 0
        # A wall-clock backstop, the same one stream_hook.drain_events has
        # carried all along on the structurally identical loop next door. Its
        # docstring - "a wedged worker must not hold an HTTP response open
        # forever" - applied here word for word, and here there was no
        # deadline at all. The worker's own 180 s budget catches the common
        # stalls, but a sentence queued behind the `_turn` lock, or one that
        # triggers host.load() mid-utterance, hits no ceiling anywhere.
        deadline = anyio.current_time() + stream_hook.DRAIN_TIMEOUT_S
        try:
            speaker.feed(text)
            speaker.finish()
            while True:
                if anyio.current_time() >= deadline:
                    # Coded, not a silent stop: audio that simply stops is
                    # indistinguishable from a reply that had nothing more
                    # to say - the same rule the error branch below follows.
                    yield _tts_sse({"type": "voice_error",
                                    "code": TTS_SYNTHESIS_FAILED})
                    return
                sent_any = False
                for note in stream_hook._host_notes():
                    yield _tts_sse({"type": "voice_notice", "note": note})
                    sent_any = True
                for chunk in speaker.drain():
                    if index == 0:
                        # The ONE number a listener actually experiences, split
                        # into the two halves that have different fixes: setup
                        # is vault reads and model resolution, synthesis is the
                        # engine (and any load it had to wait for). Logged once
                        # per utterance, at the moment the first sound becomes
                        # available - every later chunk is covered by playback
                        # of this one.
                        _log_first_audio(
                            requested_at, prepared_at, engine_was_ready,
                            len(text), chunk.get("seconds"))
                    yield _tts_sse({
                        "type": "voice_chunk",
                        "audio_id": chunk.get("audio_id") or stream_hook._stem(chunk.get("path")),
                        "seconds": chunk.get("seconds"),
                        "index": index,
                    })
                    index += 1
                    sent_any = True
                err = speaker.take_error()
                if err is not None:
                    # One sentence failing stops the utterance and the client is
                    # told which one it was. Audio that simply stopped is
                    # indistinguishable from a reply that had nothing more to say.
                    yield _tts_sse({"type": "voice_error",
                                    "code": _code_for_error(err)})
                    return
                if speaker.finished:
                    break
                if not sent_any:
                    # Off the event loop, not a busy wait: synthesis is seconds
                    # long and every other request in the app shares this loop.
                    await anyio.sleep(_DRAIN_TICK_S)
            # Same shape as SpeakHook.done_event, and for the same reason: a
            # client that has just been told the speech is complete is exactly
            # the one that needs to know the text was cut at 5000 characters
            # or that a line of it was never spoken. This endpoint is the ONLY
            # one the Speak button calls, and it reported neither.
            done: dict = {"type": "voice_done", "count": index}
            if truncated:
                done["truncated"] = True
            if speaker.dropped:
                done["dropped"] = speaker.dropped
                done["dropped_samples"] = speaker.dropped_samples
            yield _tts_sse(done)
        finally:
            # The worker thread outlives the generator otherwise - a client that
            # navigates away mid-utterance would leave it synthesising into a
            # queue nobody will ever drain.
            #
            # cancel() first and OFF THE LOOP second. cancel() is a flag, so it
            # returns at once; close() JOINS the worker, and a join that lands
            # while the engine is mid-sentence blocks for as long as that
            # sentence takes. This runs in the generator's cleanup, which is on
            # the event loop - so doing it inline would freeze every other
            # request in the app, exactly as the vault-lock path documents.
            speaker.cancel()
            await anyio.to_thread.run_sync(speaker.close)

    return StreamingResponse(event_source(), media_type="text/event-stream",
                             headers=_STREAM_SSE_HEADERS)


def _log_first_audio(requested_at: float, prepared_at: float,
                     engine_was_ready: bool, chars: int,
                     audio_seconds: float | None) -> None:
    """Report how long the listener waited, and which half of the path it went to.

    `setup` is the vault read, the model resolution and the settings; `engine`
    is synthesis plus any model load the request had to sit behind. They have
    entirely different fixes, and a single elapsed figure cannot tell them
    apart - which is why "engine was ready / not loaded" is on the line as
    well. The speech length is there so the ratio can be read directly: the
    same 5 seconds means something different in front of 3 seconds of audio
    than in front of 15.

    Best-effort by construction. This runs inside the streaming generator,
    where a raised exception ends the utterance, and a diagnostic must never be
    the reason somebody's reply stopped talking.
    """
    try:
        now = time.perf_counter()
        logger.info(
            "tts: first audio in %.2fs (setup %.2fs, engine %.2fs, "
            "%d chars in, %.2fs of speech out, engine was %s)",
            now - requested_at, prepared_at - requested_at, now - prepared_at,
            chars, float(audio_seconds or 0.0),
            "ready" if engine_was_ready else "not loaded",
        )
    except Exception:                                    # noqa: BLE001
        pass


def _tts_sse(obj: dict) -> str:
    """One data-only SSE event, the same encoding the completion stream uses."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _code_for_error(exc: BaseException) -> str:
    return stream_hook._code_for(exc)


def _speak_in_sentences(host, text: str, values: dict, extra: dict | None,
                        *, supports_tags: bool = False,
                        density: int | None = None,
                        tone: str = "",
                        message_id: int | None = None) -> dict:
    """Synthesise a whole message the way the LIVE path does: sentence by
    sentence, then joined into one file.

    One engine call per MESSAGE hits the engine's own output cap.
    `max_new_tokens` defaults to 800 semantic tokens, which on Fish S2 is about
    37 seconds of speech - so pressing Speak on a four-paragraph reply produced
    a wav that simply stopped near the end of the second paragraph. Nothing
    reported it: the audio ended, and it sounded exactly like a reply that had
    finished. Measured on the real app at 37.1 s against a 37.2 s budget.

    Splitting is also what make_stream_synth's docstring already requires of
    this path - "deliberately the SAME path /speak takes ... Two code paths
    would drift, and the drift would be audible". The live path has always
    split; only this one did not.

    The delivery dials are applied HERE, per sentence, sharing one budget
    (KÖK 6). Doing it upstream on the whole message is what dropped the
    standing tone from every sentence but the first: apply_default_tone puts
    one prefix in front of the text, and then this function cut the text into
    pieces the prefix could not reach.

    Returns the same shape host.speak() does, so the caller is unchanged.
    """
    import voice_tags

    budget = voice_tags.TagBudget(
        voice_tags.MAX_TAGS_PER_REPLY if density is None else density,
    )
    free_tags = speech_prep.injected_tags(_closing_tag(tone))

    def _dress(sentence: str) -> str:
        spoken = voice_tags.sanitize_for_tts(
            sentence, engine_supports_tags=supports_tags, budget=budget,
            # The narration tags came from prepare(), not from the model, so
            # they do not spend the model's allowance - see sanitize_for_tts.
            free_tags=free_tags)
        return voice_tags.apply_default_tone(
            spoken, tone, engine_supports_tags=supports_tags)

    sentences = [s for s in speech_prep.sentences(text) if s.strip()]
    if len(sentences) <= 1:
        return host.speak(_dress(text), values, extra=extra,
                          message_id=message_id)

    # A sentence that sanitises down to nothing (a lone malformed span) is
    # skipped rather than sent: an engine asked to speak "" fails the whole
    # utterance for a fragment that had no words in it to begin with.
    spoken = [d for d in (_dress(s) for s in sentences) if d.strip()]
    if not spoken:
        return host.speak(text, values, extra=extra,
                          message_id=message_id)

    parts = [host.speak(sentence, values, extra=extra,
                        message_id=message_id)
             for sentence in spoken]
    joined = _join_wavs([p.get("path", "") for p in parts])
    if joined is None:
        # Could not stitch them: the first part alone is still a real answer,
        # and it is the same thing this endpoint returned before.
        return parts[0]
    seconds = sum(float(p.get("seconds") or 0.0) for p in parts)
    return {**parts[-1], "path": joined, "seconds": seconds}


def _join_wavs(paths: list[str]) -> str | None:
    """Concatenate same-format wavs into one, with stdlib `wave`.

    Every engine writes 44.1 kHz mono 16-bit here, so this is a frame copy and
    nothing is resampled or re-encoded. Returns None if anything about them
    disagrees - a joined file that quietly changed format would be worse than
    not joining at all.
    """
    import wave

    real = [Path(p) for p in paths if p and Path(p).is_file()]
    if len(real) < 2:
        return None
    out_path = real[0].with_name(real[0].stem + "-joined.wav")
    try:
        params = None
        with wave.open(str(out_path), "wb") as out:
            for src in real:
                with wave.open(str(src), "rb") as part:
                    if params is None:
                        params = part.getparams()
                        out.setnchannels(params.nchannels)
                        out.setsampwidth(params.sampwidth)
                        out.setframerate(params.framerate)
                    elif (part.getnchannels(), part.getsampwidth(),
                          part.getframerate()) != (params.nchannels,
                                                   params.sampwidth,
                                                   params.framerate):
                        raise ValueError("parts disagree about format")
                    out.writeframes(part.readframes(part.getnframes()))
    except Exception:                                    # noqa: BLE001
        logger.warning("tts: could not join the spoken sentences", exc_info=True)
        secure_delete.discard(out_path)
        return None
    left = []
    for src in real:                                     # the parts are spent
        # Same file class as every other speak-*.wav: the conversation, in the
        # clear, as audio. Spent is not the same as gone.
        if not secure_delete.discard(src):
            left.append(src.name)
    if left:
        # Every other caller in this app reports what it could not remove.
        # This one used to be the exception, which is how a locked wav of the
        # conversation stayed readable with nothing anywhere saying so.
        logger.warning("tts: %d spoken part(s) could not be removed: %s",
                       len(left), ", ".join(left[:5]))
    return str(out_path)


def _dsp_noop(rate: float) -> bool:
    """Is this rate close enough to 1.0 that the worker should skip the pass?

    Mirrors `tts/worker/_dsp.is_noop`, which cannot be imported here - it lives
    in the worker tree and the app venv has no numpy. The threshold is the same
    number in both files and `test_worker_dsp.py` asserts they agree.
    """
    return abs(speed.clamp(rate) - 1.0) < 0.02


def _closing_tag(tone: str) -> str:
    """What ENDS a narration span - the standing tone when there is one.

    A direction stands until the next tag, so tagging narration means the
    dialogue after it needs a direction of its own or it inherits the
    narrator's measured, detached delivery. The obvious closing tag is a
    generic "in character, natural", and that is what this returned first -
    which quietly OVERRODE the voice the user had configured. Worse where the
    sentence opened with narration: `apply_default_tone` only prefixes text
    that does not already start with a tag, so the standing tone never reached
    the engine at all. The app replacing a user's setting with a hard-coded
    default is the same failure the tag budget had, one layer up.

    So: the tone closes its own narration. Falling back only when it is unset,
    or when it is unusable AS A TAG - `sanitize_tone` allows 60 characters and
    `_looks_like_tag` allows 40 and six words, so a long tone is a perfectly
    good setting that would be dropped as a malformed span, leaving the
    narrator's direction standing over the dialogue with nothing to say why.

    WHAT IT COSTS, measured rather than waved away: on a four-span roleplay
    reply whose dialogue is mostly untagged, three closing directions are
    emitted and the text handed to the engine grows 26% - 93 characters on 356.
    None of it is SPOKEN, so the audio is the same length and the fitted
    `c + RTF * audio` model does not see it at all; it lands on the prefill and
    on `_fit_tokens`, where a longer prompt leaves fewer tokens to generate
    with. Against a 2048-token cache and an 800-token budget that is not
    binding, and it is well under the +-0.3 s spread `verify_tts_latency`
    already shows between identical runs - which is to say the instrument
    cannot resolve it, not that it is zero. Nothing is emitted where the model
    supplied its own direction, which is the common case for tagged dialogue.
    """
    import voice_tags

    clean = voice_tags.sanitize_tone(tone or "")
    if clean and voice_tags.usable_as_tag(clean):
        return clean
    return speech_prep.DEFAULT_SPEECH_TAG


def make_stream_synth(uid: str | None = None, *, rate: float | None = None,
                      message_id: int | None = None,
                      stream_token: str | None = None):
    """A `synth(text) -> {path, seconds}` callable for the streaming speaker.

    The queue must not know what an engine is - that is what keeps its timing
    and failure paths testable without a GPU. This is the one place that turns
    "some words" into audio, and it is deliberately the SAME path `/speak`
    takes: model resolution, the vault-stored settings, the reference voice and
    the tag policy all behave identically whether a reply is spoken live or
    replayed later from its message id. Two code paths would drift, and the
    drift would be audible - the same sentence in two different voices.

    The engine is resolved ONCE, when the speaker is created, rather than per
    sentence: a model swap halfway through a reply would change voice mid-
    paragraph, which is worse than finishing in the voice that started.
    """
    import voice_tags

    model = _resolve(uid)
    adapter = adapter_for(model.engine_id)
    supports_tags = bool(adapter and adapter.capabilities.inline_prosody_tags)
    values, extra = _expand_reference(model, _values_for(model))
    specs = adapter.describe_settings(model) if adapter else []
    # No explicit rate on the request means "use the dial", NOT "use 1.0":
    # clamp(None) is DEFAULT_RATE, and merging that over the model's values
    # overrode a saved speed for live speech only.
    if rate is None:
        rate = _stored_rate()
    values = {**values, **speed.engine_values(specs, rate)}
    dsp_rate = speed.plan(specs, rate).dsp_rate

    # Read once per utterance, exactly as /speak does. Without these the density
    # and standing-tone dials applied ONLY on replay, so the same sentence
    # sounded different live than when the Speak button repeated it - the exact
    # drift this factory exists to prevent.
    density, tone = _tag_prefs()
    # ONE budget for the whole reply (KÖK 6). This closure is called once per
    # SENTENCE, so a call-local cap reset on every full stop: the density dial
    # meant "per reply" on the Speak button and "per sentence" here, and the
    # same six-tag message came out with six tags live and three on replay.
    budget = voice_tags.TagBudget(density)

    closing = _closing_tag(tone)
    free_tags = speech_prep.injected_tags(closing)

    def synth(text: str) -> dict:
        spoken = voice_tags.sanitize_for_tts(
            text, engine_supports_tags=supports_tags, budget=budget,
            # Same exemption the replay path makes, for the same reason: two
            # answers to "does narration cost the model a tag" would be the
            # audible drift this factory exists to prevent.
            free_tags=free_tags)
        spoken = voice_tags.apply_default_tone(
            spoken, tone, engine_supports_tags=supports_tags)
        if not spoken.strip():
            raise ValueError("nothing to speak")
        host = _host()
        snap = host.snapshot()
        if snap["state"] != "loaded" or snap["uid"] != model.uid:
            host.load(model, values)
        payload = dict(extra or {})
        if not _dsp_noop(dsp_rate):
            payload["rate"] = dsp_rate
        result = host.speak(spoken, values, extra=payload,
                            message_id=message_id,
                            stream_token=stream_token)
        path = Path(result.get("path", ""))
        return {
            "path": str(path),
            "audio_id": path.stem,
            "seconds": float(result.get("seconds") or 0.0),
            "sample_rate": result.get("sample_rate"),
        }

    synth.engine_supports_tags = supports_tags
    # Carried ON THE SYNTH, the way `uid` and `engine_supports_tags` already
    # are: StreamSpeaker builds the queue and would otherwise need this
    # threaded through open_speaker and SpeakHook to reach PrepOptions.
    synth.speech_tag = closing
    synth.uid = model.uid
    return synth


class SpeakLiveBody(BaseModel):
    chat_id: int


@router.post("/speak_live")
def voice_speak_live(body: SpeakLiveBody) -> dict:
    """Start speaking the reply that is streaming in this chat RIGHT NOW.

    `/speak` cannot do this. It works from a `message_id`, and during a stream
    the assistant row does not exist yet - it is written after the last delta,
    deliberately, because an assistant row opened up front and then abandoned
    resurrects an emptied chat. The client cannot send the text either: it only
    ever holds the stripped view, and the delivery tags that make the voice
    worth hearing live in the raw text.

    So the streaming endpoint keeps the raw text in a dormant hook and this
    wakes it. The audio then arrives on the SSE stream the client is already
    reading, as `voice_chunk` events - there is nothing to return here but
    whether it started.

    404 rather than a quiet 200: a Speak button that did nothing and said
    nothing is the one outcome the user cannot diagnose.
    """
    if not stream_hook.enable_live(int(body.chat_id)):
        raise HTTPException(404, "tts_nothing_streaming")
    return {"speaking": True}


#: The three ways narration can be voiced. Shared by the request body's
#: validator and by _narrative_pref's fallback, so "what is a valid mode"
#: has one answer.
NARRATIVE_MODES = ("same", "narrator", "skip")


class TagPrefsBody(BaseModel):
    density: int | None = None
    tone: str | None = None
    speed: float | None = None
    #: KÖK 6: _narrative_pref has read tts_narrative all along and NOTHING
    #: wrote it. The live stream carried the choice in its request body, so
    #: picking "Skip" worked while a reply arrived and was silently ignored
    #: the moment the same message was replayed with the Speak button.
    narrative: str | None = None
    #: Silence between sentences, seconds. Playback only - see SETTING_SENTENCE_GAP.
    gap: float | None = None


@router.get("/tag-prefs")
def get_tag_prefs() -> dict:
    density, tone = _tag_prefs()
    import voice_tags
    return {
        "density": density,
        "tone": tone,
        "narrative": _narrative_pref(),
        "narrative_modes": list(NARRATIVE_MODES),
        "gap": _stored_gap(),
        "gap_min": voice_tags.GAP_MIN,
        "gap_max": voice_tags.GAP_MAX,
        "min": voice_tags.TAG_DENSITY_MIN,
        "max": voice_tags.TAG_DENSITY_MAX,
        "tone_max_chars": voice_tags.MAX_TONE_CHARS,
        # The reading-speed dial. matrix.APP_LEVEL tells the user it is "set
        # under Delivery"; this is where Delivery reads and writes it. Nothing
        # read or wrote SETTING_SPEED before, so the whole feature - speed.py,
        # the worker WSOLA path, the greyed row pointing at it - was unreachable.
        "speed": _stored_rate() or speed.DEFAULT_RATE,
        "speed_min": speed.MIN_RATE,
        "speed_max": speed.MAX_RATE,
    }


@router.post("/tag-prefs")
def set_tag_prefs(body: TagPrefsBody) -> dict:
    """The delivery dials: how many tags a reply may keep, and a standing tone.

    The tone is the answer to "make the voice deeper/slower/closer" WITHOUT
    hunting for a new reference clip - the reference gives timbre, tags give
    performance. It is sanitised on the way in AND again on the way out: a
    bracket smuggled in here would close the span early and the rest of the
    sentence would be read aloud as text.
    """
    import voice_tags
    from database import get_db

    updates: list[tuple[str, str]] = []
    if body.density is not None:
        value = max(voice_tags.TAG_DENSITY_MIN,
                    min(voice_tags.TAG_DENSITY_MAX, int(body.density)))
        updates.append((voice_tags.SETTING_TAG_DENSITY, str(value)))
    if body.tone is not None:
        updates.append((voice_tags.SETTING_DEFAULT_TONE,
                        voice_tags.sanitize_tone(body.tone)))
    if body.speed is not None:
        updates.append((voice_tags.SETTING_SPEED,
                        f"{speed.clamp(body.speed):.3f}"))
    if body.gap is not None:
        value = max(voice_tags.GAP_MIN,
                    min(voice_tags.GAP_MAX, float(body.gap)))
        updates.append((voice_tags.SETTING_SENTENCE_GAP, f"{value:.3f}"))
    if body.narrative is not None:
        mode = str(body.narrative).strip()
        if mode not in NARRATIVE_MODES:
            raise HTTPException(422, "tts_invalid_narrative")
        updates.append((voice_tags.SETTING_NARRATIVE, mode))
    if updates:
        with get_db() as con:
            for key, value in updates:
                con.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
    return get_tag_prefs()


class PronunciationsBody(BaseModel):
    """The WHOLE table, not a patch.

    Editing reading rules is a list operation - people remove entries as often
    as they add them - and a merge-only endpoint makes deletion impossible to
    express. The client sends what the list should be.
    """

    pronunciations: dict[str, str]


@router.get("/pronunciations")
def get_pronunciations() -> dict:
    import voice_tags

    return {
        "pronunciations": stored_pronunciations(),
        "max_entries": voice_tags.MAX_PRONUNCIATIONS,
        "max_chars": voice_tags.MAX_PRONUNCIATION_CHARS,
    }


@router.post("/pronunciations")
def set_pronunciations(body: PronunciationsBody) -> dict:
    """How the user says a name, so the engine says it too.

    This is the setting the whole `pronunciations` parameter existed for: it
    was threaded through speech_prep, SpeechQueue, SpeakHook and open_speaker,
    unit-tested at every layer, and no production caller ever passed one - so
    a character named "Aoife" was mispronounced in every reply, with nothing
    in Settings to fix it.
    """
    import voice_tags
    from database import get_db

    cleaned = voice_tags.sanitize_pronunciations(body.pronunciations)
    with get_db() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (voice_tags.SETTING_PRONUNCIATIONS,
             json.dumps(cleaned, ensure_ascii=False)),
        )
    return get_pronunciations()


@router.get("/audio/{audio_id}")
def voice_audio(audio_id: str):
    if not _AUDIO_ID.match(audio_id or ""):
        raise HTTPException(404, TTS_AUDIO_EXPIRED)
    cache = Path(config.TTS_CACHE_DIR).resolve()
    target = (cache / f"{audio_id}.wav").resolve()
    # Confine: the id comes from a client, and ".." must not be able to walk
    # out of the cache into the rest of the disk.
    if not (target == cache or cache in target.parents) or not target.is_file():
        raise HTTPException(404, TTS_AUDIO_EXPIRED)
    # no-store: this is the user's conversation as audio. The embedded browser
    # runs a PERSISTENT profile (run_app.py keeps cosmetic state across
    # launches), and a heuristically-cacheable 200 would let WebView2 keep a
    # copy on disk that outlives our own wipe-on-lock.
    return FileResponse(
        str(target), media_type="audio/wav",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                 "Pragma": "no-cache"},
    )


# ── setting the engine up ────────────────────────────────────────────────────

def _known_engine(engine_id: str) -> str:
    if engine_id not in {a.engine_id for a in all_adapters()}:
        raise HTTPException(400, "tts_engine_unknown")
    return engine_id


def _provision_error(exc: provision.ProvisionError):
    raise HTTPException(500 if exc.code.endswith("failed") else 409, exc.code)


@router.get("/runtimes/{engine_id}/plan")
def runtime_plan(engine_id: str) -> dict:
    """What setup is about to do. Shown BEFORE the user commits to a multi-GB
    download, because a progress bar that appears with no warning is how a
    metered connection gets eaten. Includes `gpu_available` so the UI can warn
    that the engine will not run here before 3 GB come down."""
    _known_engine(engine_id)
    try:
        return provision.plan_payload(engine_id)
    except provision.ProvisionError as exc:
        _provision_error(exc)


@router.post("/runtimes/{engine_id}/install")
def runtime_install(engine_id: str) -> dict:
    _known_engine(engine_id)
    # A reinstall swaps the environment out from under anything running in it.
    # Same guard as DELETE: stop the worker first, or a loaded .pyd turns the
    # swap into "Access is denied" halfway through.
    host = _host()
    if host.snapshot()["engine_id"] == engine_id:
        host.unload("engine is being reinstalled")
    try:
        return provision.start_install(engine_id)
    except provision.ProvisionError as exc:
        _provision_error(exc)


@router.get("/runtimes/{engine_id}/install")
def runtime_install_status(engine_id: str) -> dict:
    _known_engine(engine_id)
    return provision.job(engine_id)


@router.post("/runtimes/{engine_id}/install/cancel")
def runtime_install_cancel(engine_id: str) -> dict:
    _known_engine(engine_id)
    return provision.cancel(engine_id)


@router.delete("/runtimes/{engine_id}")
def runtime_uninstall(engine_id: str) -> dict:
    _known_engine(engine_id)
    # Removing the environment out from under a running worker is how "Access
    # is denied" happens on a loaded .pyd - stop it first.
    host = _host()
    if host.snapshot()["engine_id"] == engine_id:
        host.unload("engine is being removed")
    try:
        return provision.uninstall(engine_id)
    except provision.ProvisionError as exc:
        _provision_error(exc)


# ── reference voices (the clips a model clones from) ─────────────────────────
# Plain files under <data>/voice/refs/ - the user's own recordings, never
# uploaded anywhere, transcribed (when at all) on this machine.

def _ref_error(exc: refs.RefError):
    raise HTTPException(_STATUS.get(exc.code, 400), exc.code)


@router.get("/voices")
def list_voices() -> dict:
    return {"voices": [v.to_json() for v in refs.list_voices()]}


@router.post("/voices/{voice_id}")
async def upload_voice(voice_id: str, file: UploadFile = File(...),
                       label: str = Form(""), transcript: str = Form("")) -> dict:
    # One byte past the cap, never the whole body. The same shape
    # uploads.py:70-73 uses, and for the same reason: the ceiling used to be
    # measured inside refs.save_upload, which is AFTER the entire file is in
    # memory, so the check could only ever report a body that had already
    # been buffered.
    #
    # Cut here rather than leaving it to refs: a truncated byte string reads
    # as a malformed audio header down there, and the user would be told the
    # file is invalid when what is true is that it is too big.
    data = await file.read(int(config.TTS_REF_MAX_BYTES) + 1)
    if len(data) > int(config.TTS_REF_MAX_BYTES):
        raise HTTPException(400, refs.TTS_REFERENCE_INVALID)
    try:
        # Off the event loop (audit KÖK 8): save_upload makes a directory,
        # writes the clip, SHREDS the previous one (a full overwrite pass) and
        # writes two metadata files. A user can do this mid-conversation, and
        # on the loop all of that froze whatever reply was streaming.
        voice = await anyio.to_thread.run_sync(
            partial(refs.save_upload, voice_id, file.filename or "", data,
                    label=label, transcript=transcript)
        )
    except refs.RefError as exc:
        _ref_error(exc)
    return voice.to_json()


class TranscriptBody(BaseModel):
    text: str = ""


@router.post("/voices/{voice_id}/transcript")
def set_voice_transcript(voice_id: str, body: TranscriptBody) -> dict:
    """The words in the clip - always editable, because an auto transcript is
    a first draft (Whisper heard "your mind" where a clip said "you're mine",
    and a wrong transcript degrades the clone without ever erroring)."""
    try:
        return refs.set_transcript(voice_id, body.text, source="user").to_json()
    except refs.RefError as exc:
        _ref_error(exc)


@router.post("/voices/{voice_id}/transcribe")
def transcribe_voice(voice_id: str) -> dict:
    """Ask the LOADED engine to hear the clip and draft its transcript.

    Runs entirely on this machine, through the engine's own runtime. Engines
    that do not transcribe say so with a code instead of pretending - the
    transcript stays manual there.
    """
    try:
        voice = refs.describe(voice_id)
    except refs.RefError as exc:
        _ref_error(exc)
    host = _host()
    # The docstring above has always PROMISED this check; nothing performed it.
    # All three workers refuse OP_TRANSCRIBE with CODE_WORKER_FAILED, which
    # _fail turned into a 500 that the frontend reads as "The voice engine
    # could not start" - about an engine that had just synthesised a reply.
    snap = host.snapshot()
    if snap.get("state") == "loaded" and not _engine_transcribes(snap.get("uid")):
        # Only once something IS loaded: with nothing loaded the honest answer
        # is still "load a model first", and host.request below gives it.
        raise HTTPException(409, TTS_TRANSCRIBE_UNSUPPORTED)
    try:
        result = host.request(
            "transcribe",
            {"path": str(Path(voice.path) / voice.audio_name)},
        )
    except WorkerFailure as exc:
        _fail(exc)
    text = str(result.get("text") or "").strip()
    if not text:
        raise HTTPException(500, TTS_SYNTHESIS_FAILED)
    try:
        return refs.set_transcript(voice_id, text, source="auto").to_json()
    except refs.RefError as exc:
        _ref_error(exc)


@router.delete("/voices/{voice_id}")
def delete_voice(voice_id: str) -> dict:
    removed = True
    try:
        removed = refs.delete(voice_id)
    except refs.RefError as exc:
        _ref_error(exc)
    if not removed:
        # WITHOUT the id. A voice_id is opaque for anything created since the
        # voice folders were hashed (the frontend mints a uuid), but every
        # voice created before that carries a slug of the label the user
        # typed, and this log line cannot tell the two apart. A label is a
        # name on screen, and tts/refs.py went to the trouble of keeping
        # those off the disk; writing one into elysium.log next to the vault
        # would hand back exactly what that bought. Which voice it was is on
        # screen in front of whoever pressed delete.
        logger.warning(
            "tts: a reference voice could not be removed; files remain on "
            "disk and it will reappear in the list.")
    # Carries the real answer now. Still a 200: the request was understood and
    # a best-effort delete is the correct behaviour for a file another process
    # may hold - what was wrong was claiming it worked.
    return {"voice_id": voice_id, "removed": removed}
