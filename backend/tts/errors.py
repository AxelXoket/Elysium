"""tts/errors.py - the single source of the voice error vocabulary.

Every code here must exist in FOUR places at once, or a failure reaches the user
as a generic "something went wrong":
  1. here (backend raises it as the HTTPException detail - the detail IS the code)
  2. frontend/src/lib/errors/errorMessages.ts   (human text)
  3. docs/frontend_contract.md                  (the contract table)
  4. frontend ErrorHandling.test.ts             (asserts no code falls back)

Voice never blocks chatting: every one of these turns voice off and says why.
Silence is the one outcome that is not allowed.
"""
from __future__ import annotations

# ── discovery / identity ─────────────────────────────────────────────────────
# Reserved: defined for the contract but not yet raised by a live path.
# tts_model_not_found waits for a "speak with no models at all" flow (V5) and
# tts_audio_device_error for frontend playback (V5) - listed here so the
# vocabulary is complete in all four places before the UI needs them.
TTS_MODEL_NOT_FOUND = "tts_model_not_found"          # 404 nothing in the folder
TTS_MODEL_UNKNOWN = "tts_model_unknown"              # 400 uid no longer resolves
TTS_MODEL_UNRECOGNIZED = "tts_model_unrecognized"    # 409 no engine signature matched
TTS_MODEL_INCOMPLETE = "tts_model_incomplete"        # 422 engine known, files missing
TTS_ENGINE_UNKNOWN = "tts_engine_unknown"            # 400 engine id not registered
TTS_RUNTIME_MISSING = "tts_runtime_missing"          # 409 never set up
TTS_RUNTIME_BROKEN = "tts_runtime_broken"            # 409 set up once, gone now
TTS_RUNTIME_UNTRUSTED = "tts_runtime_untrusted"      # 409 set up, then changed

# ── compatibility (not failures - things the user must know BEFORE trying) ──
# Voice must never be a surprise. A model is always inspectable, so these say
# what would stop it, in the same breath as showing its settings.
TTS_GPU_UNAVAILABLE = "tts_gpu_unavailable"          # 409 no readable NVIDIA GPU
TTS_LANGUAGE_UNSUPPORTED = "tts_language_unsupported"  # warning, never a block

# ── runtime provisioning (the app installs it; the user never opens a shell) ─
TTS_RUNTIME_INSTALLING = "tts_runtime_installing"    # 409 one install at a time
TTS_RUNTIME_INSTALL_FAILED = "tts_runtime_install_failed"  # 500 with a real reason
TTS_PYTHON_NOT_FOUND = "tts_python_not_found"        # 409 nothing to build an env from
TTS_INSUFFICIENT_DISK = "tts_insufficient_disk"      # 409 multi-GB install will not fit

# ── settings ────────────────────────────────────────────────────────────────
TTS_PARAM_INVALID = "tts_param_invalid"              # 400 value out of range/choice
TTS_SIDECAR_WRITE_FAILED = "tts_sidecar_write_failed"  # 500 model folder not writable
TTS_VALUES_TOO_LARGE = "tts_values_too_large"        # 400 payload over the cap

# ── load / lifecycle ────────────────────────────────────────────────────────
TTS_INSUFFICIENT_VRAM = "tts_insufficient_vram"      # 409 pre-load check refused
TTS_MODEL_ALREADY_LOADING = "tts_model_already_loading"  # 409 one slot only
TTS_LOAD_TIMEOUT = "tts_load_timeout"                # 504 compile can be slow, not forever
TTS_WORKER_FAILED = "tts_worker_failed"              # 500 could not start
TTS_WORKER_CRASHED = "tts_worker_crashed"            # 500 died unexpectedly
TTS_WORKER_UNAVAILABLE = "tts_worker_unavailable"    # 500 nothing loaded
# Distinct from tts_insufficient_vram on purpose: that one means "we refused
# before starting", this one means "it started and ran out". Different advice -
# the second is fixed by a smaller cache, not by closing a game.
TTS_OUT_OF_MEMORY = "tts_out_of_memory"              # 500 CUDA OOM mid-flight

# ── synthesis ───────────────────────────────────────────────────────────────
TTS_SYNTHESIS_FAILED = "tts_synthesis_failed"        # 500 generation failed
TTS_REFERENCE_INVALID = "tts_reference_invalid"      # 400 clip unusable
TTS_REFERENCE_TOO_SHORT = "tts_reference_too_short"  # 400 fixable in one sentence
# The voice folder is a junction or a symlink, so saving there would write the
# user's recording into somebody else's directory and replacing a clip would
# delete somebody else's audio. Its own code rather than tts_reference_invalid:
# nothing is wrong with the CLIP, and telling somebody their recording is
# unusable sends them off to re-record a perfectly good take.
TTS_REFERENCE_FOLDER_REDIRECTED = "tts_reference_folder_redirected"  # 400
# The clip already saved for this voice could not be destroyed - something has
# the file open (the engine mid-sentence, an antivirus mid-scan). The upload is
# refused rather than completed, because a surviving old clip does not step
# aside politely: it goes on BEING the voice while the new transcript describes
# the new take. Its own code, not tts_reference_invalid: the clip that was just
# sent is fine, and the fix is to wait or close something, not to re-record.
TTS_REFERENCE_CLIP_STUCK = "tts_reference_clip_stuck"  # 409 try again shortly
# Fish cannot clone from audio alone - it needs the words that were said.
TTS_TRANSCRIPT_REQUIRED = "tts_transcript_required"  # 400 give one, or let us hear it
# No shipped engine has ASR. Distinct from tts_worker_failed on purpose: that
# one means the engine broke, this one means the engine is fine and simply
# does not do this. They deserve opposite sentences, and for a long time both
# came out as "The voice engine could not start".
TTS_TRANSCRIBE_UNSUPPORTED = "tts_transcribe_unsupported"  # 409 type it yourself
# Three failures used to share tts_synthesis_failed, which the contract
# documents as one 500 and which says "the voice could not be generated for
# this message". Two of them are not that:
TTS_NOTHING_TO_SPEAK = "tts_nothing_to_speak"        # 400 the text had no words
# The wav is gone, not un-generatable: the cache is wiped when the vault locks
# and does not outlive the session on purpose. Sent to the frontend it used to
# read as a synthesis failure, and through the audio player as "no audio output
# device" - so a restart sent people hunting for a sound-card driver.
TTS_AUDIO_EXPIRED = "tts_audio_expired"              # 404 ask for it again
TTS_AUDIO_DEVICE_ERROR = "tts_audio_device_error"    # 500 no output device
# The audio cache folder resolves outside the app's own data directory - a
# junction, a mount point, an absolute path from somewhere. The spoken reply is
# chat content, and chat content does not get written permanently outside the
# encrypted database's own folder. Refused rather than written, because every
# sweep that would later remove it refuses a redirected name too, so writing
# there means leaving it there.
TTS_CACHE_OUTSIDE_DATA_DIR = "tts_cache_outside_data_dir"  # 500
# Speak pressed mid-reply, but that reply is no longer streaming (it finished,
# or was aborted). A quiet 200 here would leave a button that did nothing and
# said nothing - the one outcome nobody can diagnose by looking at it.
TTS_NOTHING_STREAMING = "tts_nothing_streaming"      # 404 that reply is over

ALL_CODES: frozenset[str] = frozenset({
    TTS_MODEL_NOT_FOUND, TTS_MODEL_UNKNOWN, TTS_MODEL_UNRECOGNIZED,
    TTS_MODEL_INCOMPLETE, TTS_ENGINE_UNKNOWN, TTS_RUNTIME_MISSING,
    TTS_RUNTIME_BROKEN, TTS_RUNTIME_UNTRUSTED, TTS_GPU_UNAVAILABLE,
    TTS_LANGUAGE_UNSUPPORTED,
    TTS_RUNTIME_INSTALLING, TTS_RUNTIME_INSTALL_FAILED, TTS_PYTHON_NOT_FOUND,
    TTS_INSUFFICIENT_DISK,
    TTS_PARAM_INVALID, TTS_SIDECAR_WRITE_FAILED, TTS_VALUES_TOO_LARGE,
    TTS_INSUFFICIENT_VRAM, TTS_MODEL_ALREADY_LOADING, TTS_LOAD_TIMEOUT,
    TTS_WORKER_FAILED, TTS_WORKER_CRASHED, TTS_WORKER_UNAVAILABLE,
    TTS_OUT_OF_MEMORY,
    TTS_SYNTHESIS_FAILED, TTS_REFERENCE_INVALID, TTS_REFERENCE_TOO_SHORT,
    TTS_REFERENCE_FOLDER_REDIRECTED, TTS_REFERENCE_CLIP_STUCK,
    TTS_TRANSCRIPT_REQUIRED, TTS_TRANSCRIBE_UNSUPPORTED,
    TTS_NOTHING_TO_SPEAK, TTS_AUDIO_EXPIRED,
    TTS_AUDIO_DEVICE_ERROR, TTS_CACHE_OUTSIDE_DATA_DIR,
    TTS_NOTHING_STREAMING,
})


class TtsError(Exception):
    """Base for voice failures. `code` is what the client sees - never prose."""

    code: str = TTS_WORKER_FAILED

    def __init__(self, code: str | None = None, detail: str = ""):
        if code:
            self.code = code
        # detail is for the LOG only (id-only, never user content or secrets).
        self.detail = detail
        super().__init__(self.code)


class ParamError(TtsError):
    """A setting value could not be coerced into its declared spec."""

    code = TTS_PARAM_INVALID
