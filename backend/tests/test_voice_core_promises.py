"""Audit KÖK 3 + KÖK 4: the two headline voice promises, both dead in code.

"<3 s to first audio" and "a clean install can load its first model" are the
two things the voice feature is sold on. Neither could happen:

  - SpeechQueue built a NEW Pacing per reply, so the estimator was reset
    before it had measured anything and first_chunk_window() returned None on
    every reply forever. speech_prep.first_chunk() was never called in
    production at all.
  - _warmup emitted one frame and then compiled in silence for ~346 s against
    a host budget that kills the worker after 180 s of quiet.

Both are tested for the MECHANISM firing, not for a wall-clock number: the
timings are machine-specific, the plumbing is not.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tts import pacing as pacing_module
from tts.pacing import (
    FIRST_CHUNK_BUDGET_SECONDS,
    FIRST_CHUNK_MIN_CHARS,
    Pacing,
)

#: Absolute, because a test that only passes from one directory is a test that
#: will surprise somebody. Running `pytest backend/` from the repo root used to
#: fail eleven tests across four files with FileNotFoundError on a relative
#: path like 'tts/provision.py'. Measured 2026-08-10 and fixed here.
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_registry():
    pacing_module.reset_shared()
    yield
    pacing_module.reset_shared()


# ---------------------------------------------------------------------------
# 1. a fresh Pacing cannot answer, which is why it must not be fresh
# ---------------------------------------------------------------------------

def test_an_unmeasured_pacing_answers_only_when_the_arithmetic_allows_it():
    """Not a bug in Pacing either way - the honest answer for an engine it has
    never seen. The bug was arranging for it to be asked in this state every
    time.

    This asserted `is None` for the seeded Fish engine, which was true while
    its fixed cost was 1.6 s and stopped being true when that came down to 0.6.
    It was pinning a fact about one machine, not the rule - so the rule is what
    is pinned now: an engine too slow to start inside the budget says so, and
    one fast enough answers."""
    glacial = Pacing(rtf=8.0, fixed_seconds=20.0)
    assert glacial.first_chunk_window(FIRST_CHUNK_BUDGET_SECONDS) is None
    assert Pacing().first_chunk_window(FIRST_CHUNK_BUDGET_SECONDS) is not None


def test_a_measured_pacing_does_answer():
    """And this is what was being thrown away after every reply."""
    p = Pacing()
    # A fast engine: 40 chars of text -> 2.5 s of audio, made in 0.35 s.
    for _ in range(12):
        p.observe(chars=40, audio_seconds=2.5, gen_seconds=0.35)
    assert p.measured
    window = p.first_chunk_window(FIRST_CHUNK_BUDGET_SECONDS)
    assert window is not None, "a measured fast engine still refuses to cut"
    lo, hi = window
    assert lo == FIRST_CHUNK_MIN_CHARS
    assert lo <= hi


# ---------------------------------------------------------------------------
# 2. the registry: one instance per model, surviving the reply
# ---------------------------------------------------------------------------

def test_the_same_model_gets_the_same_pacing():
    assert pacing_module.for_model("aurora") is pacing_module.for_model("aurora")


def test_different_models_do_not_share_timings():
    """Swapping models must not carry the old model's speed over - that would
    be a worse estimate than having none."""
    assert pacing_module.for_model("aurora") is not pacing_module.for_model("ember")


def test_an_unidentified_engine_still_gets_one_stable_bucket():
    assert pacing_module.for_model(None) is pacing_module.for_model(None)


def test_learning_survives_across_replies():
    """The whole point, stated as the thing that used to be false."""
    first = pacing_module.for_model("aurora")
    for _ in range(12):
        first.observe(chars=40, audio_seconds=2.5, gen_seconds=0.35)

    # A second reply, later. It used to start from zero here.
    second = pacing_module.for_model("aurora")
    assert second.measured
    assert second.first_chunk_window(FIRST_CHUNK_BUDGET_SECONDS) is not None


# ---------------------------------------------------------------------------
# 3. StreamSpeaker actually wires it in
# ---------------------------------------------------------------------------

def _synth_factory(uid: str | None):
    def synth(text: str) -> dict:
        time.sleep(0)
        return {"path": f"/tmp/{uid}-{len(text)}.wav", "audio_id": "x",
                "seconds": max(0.5, len(text) * 0.06)}
    synth.uid = uid
    synth.engine_supports_tags = False
    return synth


def test_stream_speaker_takes_the_shared_pacing_for_its_model():
    from tts.stream_speech import StreamSpeaker

    shared = pacing_module.for_model("aurora")
    speaker = StreamSpeaker(_synth_factory("aurora"))
    try:
        assert speaker._queue._pacing is shared
    finally:
        speaker.cancel()
        speaker.close()


def test_two_replies_on_one_model_share_one_estimator():
    """The regression this whole group exists for: reply two must inherit
    what reply one measured."""
    from tts.stream_speech import StreamSpeaker

    first = StreamSpeaker(_synth_factory("aurora"))
    second = StreamSpeaker(_synth_factory("aurora"))
    try:
        assert first._queue._pacing is second._queue._pacing
    finally:
        for s in (first, second):
            s.cancel()
            s.close()


def test_an_explicit_pacing_still_wins():
    """Tests want a deterministic bank; the registry must not take that away."""
    from tts.stream_speech import StreamSpeaker

    mine = Pacing()
    speaker = StreamSpeaker(_synth_factory("aurora"), pacing=mine)
    try:
        assert speaker._queue._pacing is mine
    finally:
        speaker.cancel()
        speaker.close()


def test_the_first_chunk_path_is_reachable_once_the_model_is_known(monkeypatch):
    """End to end for the dead code path: with a trained shared Pacing, the
    queue asks speech_prep.first_chunk() for a real cut. It never did.
    """
    import speech_prep
    from tts.stream_speech import StreamSpeaker

    trained = pacing_module.for_model("aurora")
    for _ in range(12):
        trained.observe(chars=40, audio_seconds=2.5, gen_seconds=0.35)

    calls: list[tuple[int, int]] = []
    real_first_chunk = speech_prep.first_chunk

    def _spy(text, *, min_chars, max_chars):
        calls.append((min_chars, max_chars))
        return real_first_chunk(text, min_chars=min_chars, max_chars=max_chars)

    monkeypatch.setattr(speech_prep, "first_chunk", _spy)

    speaker = StreamSpeaker(_synth_factory("aurora"))
    try:
        speaker.feed(
            "The harbour lights had already come on by the time she reached "
            "the far end of the pier, and the wind carried salt with it."
        )
        speaker.finish()
        # Wait for the thing this test is about, not for `speaker.finished`.
        #
        # This loop used to read `while not speaker.finished`, and it burned
        # its whole ten second deadline on every single run, measured at 10.01s
        # and the slowest test in the suite. `finished` requires `not
        # self._out`, so it cannot go true until something DRAINS the audio,
        # and this test never drains: it has no consumer, by design, because
        # what it is checking is that `speech_prep.first_chunk` gets called at
        # all. The condition was unreachable by construction. Probed on
        # 2026-08-10 with a thirty second wait: still False, two chunks parked
        # in `_out`. Nothing is broken in the speaker.
        #
        # The spy IS filled off-thread, so some wait is genuinely needed; a
        # deadline on the real condition gives the same protection at a
        # thousandth of the cost, and it fails loudly instead of falling
        # through to a bare `assert calls` if the call never comes.
        deadline = time.monotonic() + 10.0
        while not calls and time.monotonic() < deadline:
            time.sleep(0.005)
    finally:
        speaker.cancel()
        speaker.close()

    assert calls, "first_chunk() was still never called in the live path"
    assert calls[0][0] == FIRST_CHUNK_MIN_CHARS


# ---------------------------------------------------------------------------
# 4. the compile heartbeat
# ---------------------------------------------------------------------------

def test_the_heartbeat_keeps_talking_through_an_opaque_operation(monkeypatch):
    """The host's budget is a SILENCE budget measured from the last progress
    frame, so a 346 s compile that says nothing is killed at 180 s - which is
    why a clean install could never finish its first load. torch.compile is
    opaque, so the frames have to come from a thread beside it."""
    import importlib.util
    import sys
    from pathlib import Path

    worker_dir = BACKEND / "tts" / "worker"
    sys.path.insert(0, str(worker_dir))
    try:
        spec = importlib.util.spec_from_file_location(
            "fish_s2_under_test", worker_dir / "fish_s2.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(worker_dir))

    monkeypatch.setattr(module, "_HEARTBEAT_SECONDS", 0.05)

    sent: list[dict] = []
    with module._heartbeat(sent.append, "compiling", 0.7):
        time.sleep(0.35)

    assert len(sent) >= 3, f"only {len(sent)} frames during the wait"
    assert all(f.get("stage") == "compiling" for f in sent)
    # Monotonic elapsed, so the host can see it is progress and not a repeat.
    elapsed = [f["elapsed_seconds"] for f in sent]
    assert elapsed == sorted(elapsed)


def test_the_heartbeat_stops_before_the_caller_sends_anything_else(monkeypatch):
    """It must JOIN on exit: two threads writing the wire concurrently would
    interleave two frames into one unparseable line."""
    import importlib.util
    import sys
    from pathlib import Path

    worker_dir = BACKEND / "tts" / "worker"
    sys.path.insert(0, str(worker_dir))
    try:
        spec = importlib.util.spec_from_file_location(
            "fish_s2_join_test", worker_dir / "fish_s2.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(worker_dir))

    monkeypatch.setattr(module, "_HEARTBEAT_SECONDS", 0.02)
    sent: list[dict] = []
    with module._heartbeat(sent.append, "compiling"):
        time.sleep(0.1)
    count = len(sent)
    time.sleep(0.15)
    assert len(sent) == count, "the heartbeat thread outlived its block"


def test_warmup_wraps_both_compile_attempts_in_a_heartbeat():
    """Including the eager retry: it runs on the machine that has just proved
    it is the slow kind, so it is the attempt LEAST able to afford silence.

    KADEME 15a: this used to slice `_warmup` out of the file as text and count
    the substring `with _heartbeat(` twice. That is satisfied by two calls in
    dead code and broken by a rename, and it cannot see whether either block
    actually emitted anything. `_warmup` runs here instead, with a compile that
    fails once, and the frames are counted from the wire - the two blocks carry
    different notes, so each attempt can be accounted for separately.
    """
    import fish_synth_harness as synth

    module = synth.load_worker()
    module._HEARTBEAT_SECONDS = 0.02
    module._engine = lambda: {"decode_eager": object()}
    module._sweep = lambda: None

    attempts: list[int] = []

    def generation(*a, **kw):
        attempts.append(1)
        time.sleep(0.12)          # long enough for the beat to be heard
        if len(attempts) == 1:
            raise RuntimeError("no MSVC toolchain on this machine")
        return None

    module._run_generation = generation

    sent: list[dict] = []
    module._warmup(sent.append)

    assert attempts == [1, 1], "the eager retry did not happen"
    assert module.STATE["compile_broken"] is True, (
        "the fallback was not recorded as a property of the machine")

    beats = [e.get("note") for e in sent if e.get("stage") == "compiling"]
    first = [n for n in beats if n == "compiling the model for this GPU"]
    retry = [n for n in beats if n == "compiling failed; retrying without it"]
    assert first, "the first compile ran without a heartbeat"
    assert retry, "the eager RETRY ran without a heartbeat - the host's silence "\
                  "budget kills it at 180s and nothing would say why"


# ---------------------------------------------------------------------------
# 5. the underrun signal: observed, not asked for
# ---------------------------------------------------------------------------

def _queue(pacing, **kw):
    from tts.speech_queue import SpeechQueue

    def synth(text: str) -> dict:
        return {"path": "/tmp/x.wav", "audio_id": "x",
                "seconds": max(0.5, len(text) * 0.05)}

    return SpeechQueue(synth=synth, pacing=pacing, **kw)


def test_an_empty_bank_mid_reply_is_recorded_as_an_underrun():
    """note_underrun/note_clean_chunk had no production caller at all, so
    Pacing._penalty stayed 0.0 for the life of the process and the estimator
    could never learn it had been too optimistic."""
    p = Pacing()
    q = _queue(p)
    q.push("First sentence here. Second sentence here.")
    q.pump()
    assert q.take() is not None          # playback begins

    while q.take() is not None:          # drain the bank
        pass
    assert p._penalty > 0.0, "the gap was never recorded"


def test_a_bank_that_keeps_up_decays_the_penalty():
    p = Pacing()
    p.note_underrun()
    before = p._penalty
    q = _queue(p)
    q.push("One. Two. Three. Four.")
    q.pump()
    q.take()
    assert p._penalty < before


def test_nothing_is_recorded_before_playback_starts():
    """An empty bank before the first chunk is the normal start condition,
    not a gap in playback."""
    p = Pacing()
    q = _queue(p)
    assert q.take() is None
    assert p._penalty == 0.0


def test_nothing_is_recorded_once_the_reply_is_finished():
    """A drained queue at the END of a reply is not an underrun - there was
    nothing more to play."""
    p = Pacing()
    q = _queue(p)
    q.push("Only one sentence.")
    q.close()
    q.pump()
    while q.take() is not None:
        pass
    assert q.finished
    assert p._penalty == 0.0


def test_the_chunk_size_rule_is_still_available_for_the_cutter():
    """max_chunk_seconds/max_chunk_chars are the module docstring's second
    rule. They are NOT deleted: the safe place to apply them is the sentence
    splitter, which is being repaired separately - wiring them to today's
    cutter would make it split more often, and today's cutter is the one that
    breaks '3.5 million' in half."""
    p = Pacing()
    for _ in range(12):
        p.observe(chars=40, audio_seconds=2.5, gen_seconds=0.35)
    assert p.max_chunk_seconds(0.0) > 0.0
    assert p.max_chunk_chars(0.0) > 0
    # Monotonic in the bank: more audio banked means a longer next chunk is
    # affordable. That is the inequality the rule is solved from.
    assert p.max_chunk_seconds(10.0) > p.max_chunk_seconds(0.0)
