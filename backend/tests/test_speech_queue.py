"""V8-3 (host half) - the sentence queue that keeps a voice talking.

The engine returns nothing until a whole utterance is done, so a five-paragraph
reply spoken in one call is five paragraphs of silence first. The queue turns
that into: split, synthesise the first sentence, start talking, keep the next
one warm while this one plays.

It is deliberately I/O-free. `synth` is a callable and `now` is injected, so
every timing and failure path below is exercised without an engine, a GPU or a
clock - which is the only way this stays testable on a machine that cannot run
the model at all.
"""
import pytest

from tts.speech_queue import SpeechQueue, QueueFailed


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def synth_ok(seconds=2.0, cost=0.0, clock=None):
    """A synth that reports `seconds` of audio per sentence."""
    calls = []

    def synth(text):
        calls.append(text)
        if cost and clock:
            clock.advance(cost)
        return {"path": f"/audio/{len(calls)}.wav", "seconds": seconds}

    synth.calls = calls
    return synth


def make(**kw):
    clock = kw.pop("clock", None) or FakeClock()
    synth = kw.pop("synth", None) or synth_ok()
    q = SpeechQueue(synth=synth, now=clock, **kw)
    return q, synth, clock


# ── splitting and lookahead ──────────────────────────────────────────────────

def drain(q):
    """Play the queue to the end the way a player would: pump, take, repeat."""
    out = []
    while True:
        q.pump()
        chunk = q.take()
        if chunk is None:
            if q.finished:
                return out
            return out                      # nothing more can be produced
        out.append(chunk)


def test_a_complete_text_is_split_and_synthesised_in_order():
    q, synth, _ = make()
    q.push("One sentence. Two sentence. Three.")
    q.close()
    drain(q)
    assert synth.calls == ["One sentence.", "Two sentence.", "Three."]


def test_a_partial_sentence_is_not_synthesised_yet():
    q, synth, _ = make()
    q.push("Hello there. How are y")
    q.pump()
    assert synth.calls == ["Hello there."]


def test_the_tail_is_released_when_the_stream_closes():
    q, synth, _ = make()
    q.push("No terminal here")
    q.pump()
    assert synth.calls == []
    q.close()
    q.pump()
    assert synth.calls == ["No terminal here"]


# The bank is PINNED in these two. pump() deliberately runs past the lookahead
# while `_may_start()` is still false - getting to a safe start beats the cap,
# or short sentences deadlock the pre-roll - so a count asserted against the
# lookahead alone is really asserting where the seeded Pacing happens to think
# a safe start is. That is a coincidence of three constants, and it broke the
# first time they were re-measured. `preroll_seconds` exists for exactly this.

def test_lookahead_is_bounded_so_a_long_reply_does_not_run_away():
    q, synth, _ = make(lookahead=2, preroll_seconds=0.0)
    q.push(" ".join(f"Sentence {i}." for i in range(1, 9)))
    q.pump()
    # Nothing has been consumed by a player yet, so only the buffer depth runs.
    assert len(synth.calls) == 2


def test_taking_a_chunk_lets_the_next_one_be_prepared():
    q, synth, _ = make(lookahead=2, preroll_seconds=0.0)
    q.push("A one. B two. C three. D four.")
    q.pump()
    assert len(synth.calls) == 2
    q.take()
    q.pump()
    assert len(synth.calls) == 3


# ── the pre-roll ─────────────────────────────────────────────────────────────

def test_playback_waits_until_the_pre_roll_is_buffered():
    # The user asked for ~2 s of head start: production runs at 1.6x realtime,
    # so once it is ahead it stays ahead - but it has to get ahead first.
    q, _, _ = make(preroll_seconds=2.0, synth=synth_ok(seconds=0.8))
    q.push("A one. B two.")
    q.pump()
    assert not q.ready()                    # 0.8 s buffered, not enough
    q.push(" C three.")
    q.close()
    q.pump()
    assert q.ready()                        # 2.4 s buffered


def test_closing_the_stream_releases_a_short_reply_below_the_pre_roll():
    # A three-word answer must not sit in the buffer forever waiting for 2 s
    # of audio that is never coming.
    q, _, _ = make(preroll_seconds=2.0, synth=synth_ok(seconds=0.4))
    q.push("Sure.")
    q.close()
    q.pump()
    assert q.ready()


def test_short_sentences_cannot_deadlock_the_pre_roll():
    """Regression: the lookahead cap must not make the pre-roll unreachable.

    With a count-only bound, short sentences saturate the buffer at three
    chunks totalling 0.9 s against a 2 s pre-roll - and then nothing moves,
    because ready() waits for audio that pump() has decided not to make and
    take() never runs to unblock it. The voice would simply never start.

    There IS enough text here to clear the pre-roll (8 x 0.3 s = 2.4 s); the
    only thing that could stop it is the cap, which is exactly what is pinned.
    """
    q, synth, _ = make(lookahead=3, preroll_seconds=2.0,
                       synth=synth_ok(seconds=0.3))
    q.push("A one. B two. C three. D four. "
           "E five. F six. G seven. H eight.")
    q.pump()
    assert q.ready()
    assert len(synth.calls) > 3             # kept going past the count bound


def test_ready_stays_true_once_playback_has_begun():
    q, _, _ = make(preroll_seconds=1.0, synth=synth_ok(seconds=0.6))
    q.push("A one. B two.")
    q.pump()
    assert q.ready()
    q.take()
    q.take()
    assert q.ready()                        # drained mid-reply, not un-ready


# ── failure policy (user decision 5: stop, never skip silently) ──────────────

def test_an_engine_failure_stops_the_whole_queue():
    def boom(text):
        if "two" in text:
            raise RuntimeError("worker died")
        return {"path": "/a.wav", "seconds": 1.0}

    q, _, _ = make(synth=boom)
    q.push("A one. B two. C three.")
    q.close()
    with pytest.raises(QueueFailed) as exc:
        q.pump()
    assert "worker died" in str(exc.value.__cause__ or exc.value)
    assert q.failed


def test_a_failed_queue_refuses_further_work_instead_of_half_speaking():
    q, synth, _ = make(synth=lambda t: (_ for _ in ()).throw(RuntimeError("x")))
    q.push("One. Two.")
    q.close()
    with pytest.raises(QueueFailed):
        q.pump()
    with pytest.raises(QueueFailed):
        q.pump()


def test_a_sentence_that_prepares_to_nothing_is_skipped_not_failed():
    # A line that is only a code fence has nothing to say; that is not an error.
    q, synth, _ = make()
    q.push("```\nx = 1\n```\nBut this matters.")
    q.close()
    q.pump()
    assert synth.calls == ["But this matters."]


# ── cancellation ─────────────────────────────────────────────────────────────

def test_cancel_stops_synthesis_and_drops_pending_audio():
    q, synth, _ = make(lookahead=1)
    q.push("A one. B two. C three.")
    q.pump()
    before = len(synth.calls)
    q.cancel()
    q.pump()
    assert len(synth.calls) == before
    assert q.take() is None


def test_cancel_is_idempotent():
    q, _, _ = make()
    q.push("A one.")
    q.cancel()
    q.cancel()
    assert q.cancelled


# ── text preparation is applied, once ────────────────────────────────────────

def test_markdown_never_reaches_the_engine():
    q, synth, _ = make()
    q.push("## Heading\n- **bold** item with [link](http://x.y).")
    q.close()
    q.pump()
    joined = " ".join(synth.calls)
    assert "#" not in joined and "*" not in joined and "http" not in joined
    assert "link" in joined


def test_tags_survive_for_a_tag_capable_engine():
    q, synth, _ = make(engine_supports_tags=True)
    q.push("[soft, close] Come here.")
    q.close()
    q.pump()
    assert "[soft, close]" in synth.calls[0]


# ── bookkeeping the player needs ─────────────────────────────────────────────

def test_take_returns_chunks_in_order_then_none_when_finished():
    q, _, _ = make()
    q.push("A one. B two.")
    q.close()
    q.pump()
    first, second = q.take(), q.take()
    assert first["text"] == "A one." and second["text"] == "B two."
    assert q.take() is None
    assert q.finished


def test_finished_is_false_while_more_text_may_still_arrive():
    q, _, _ = make()
    q.push("A one.")
    q.pump()
    q.take()
    assert not q.finished
    q.close()
    q.pump()
    assert q.finished


def test_everything_the_engine_returned_survives_into_the_chunk():
    """Regression: the chunk used to be built from a hand-picked list of keys,
    which silently dropped `audio_id` - the one field the client needs to fetch
    the audio. Nothing failed; the chunk just arrived with a null id."""
    def synth(text):
        return {"path": "/a.wav", "audio_id": "abc123", "seconds": 1.0,
                "sample_rate": 44100, "cloned": True}

    q, _, _ = make(synth=synth)
    q.push("One.")
    q.close()
    q.pump()
    chunk = q.take()
    assert chunk["audio_id"] == "abc123"
    assert chunk["sample_rate"] == 44100
    assert chunk["cloned"] is True
    # ...and our own bookkeeping still wins where the names collide.
    assert chunk["text"] == "One."


def test_buffered_seconds_reports_what_is_actually_queued():
    q, _, _ = make(synth=synth_ok(seconds=1.5), lookahead=3)
    q.push("A one. B two.")
    q.close()
    q.pump()
    assert q.buffered_seconds() == pytest.approx(3.0)
    q.take()
    assert q.buffered_seconds() == pytest.approx(1.5)


# ── the measured start, replacing the fixed pre-roll ─────────────────────────

class TestPlaybackStartsWhenItIsSafeNotAfterAFixedBank:
    """Two seconds of banked audio is plenty before a short sentence and not
    nearly enough before a long one. The queue asks which case it is in."""

    def _fast_engine(self, clock):
        """4x realtime, with the sentence length actually mattering - identical
        chunk sizes would leave the fit unable to separate the fixed cost from
        the proportional one, which is a real property of the estimator and not
        something a fake should paper over."""
        def synth(text):
            seconds = max(1.0, len(text) * 0.07)
            clock.advance(seconds / 4.0)
            return {"path": "/a.wav", "seconds": seconds}
        return synth

    def _slow_engine(self, clock):
        """Slower than realtime: 1 second of speech per 2 seconds of compute."""
        def synth(text):
            seconds = max(1.0, len(text) * 0.07)
            clock.advance(seconds * 2.0)
            return {"path": "/a.wav", "seconds": seconds}
        return synth

    def test_a_fast_engine_starts_on_the_first_chunk(self):
        """One chunk covers the next one comfortably, so waiting for a second
        would be pure added latency - which is what the fixed pre-roll did."""
        clock = FakeClock()
        q, _, _ = make(clock=clock, synth=self._fast_engine(clock))
        q.push("Short one. Another one here. And a third.")
        q.pump()
        assert q.ready() is True
        # Cold, it does not yet know it is fast, so it may bank a second chunk
        # before committing. What it must not do is keep going.
        assert len(q._chunks) <= 2

    def test_a_slow_engine_banks_more_before_starting(self):
        """Same code, same text, opposite decision - because the engine is
        different. No constant anywhere expresses this."""
        text = " ".join("Sentence " + "very " * i + "long." for i in range(10))

        def banked_at_start(engine):
            clock = FakeClock()
            q, _, _ = make(clock=clock, synth=engine(clock))
            q.push(text)
            while not q.ready():
                if q.pump() == 0:
                    break
            return q.buffered_seconds()

        assert banked_at_start(self._slow_engine) > banked_at_start(self._fast_engine)

    def test_a_closed_stream_never_waits_for_audio_that_is_not_coming(self):
        """A three-word answer must not sit behind a bank it can never fill."""
        clock = FakeClock()
        q, _, _ = make(clock=clock, synth=self._slow_engine(clock))
        q.push("Yes.")
        q.close()
        q.pump()
        assert q.ready() is True

    def test_once_started_it_stays_started(self):
        """Going un-ready mid-reply would stutter the playback the queue exists
        to smooth."""
        clock = FakeClock()
        q, _, _ = make(clock=clock, synth=self._fast_engine(clock))
        q.push("First one. Second one.")
        q.pump()
        assert q.ready() is True
        while q.take() is not None:
            pass
        assert q.buffered_seconds() == 0.0
        assert q.ready() is True

    def test_an_explicit_preroll_still_pins_the_old_behaviour(self):
        """The override exists so a test can be deterministic, and so anyone
        who genuinely wants a constant can have one."""
        clock = FakeClock()
        q, _, _ = make(clock=clock, preroll_seconds=100.0,
                       synth=self._fast_engine(clock))
        q.push("One. Two. Three.")
        q.pump()
        assert q.ready() is False

    def test_it_calibrates_itself_from_ordinary_work(self):
        """Nothing is benchmarked separately: finished chunks ARE the samples."""
        clock = FakeClock()
        q, _, _ = make(clock=clock, synth=self._fast_engine(clock))
        assert q._pacing.measured is False
        q.push("One sentence. Two sentence.")
        q.pump()
        assert q._pacing.measured is True

    def test_the_learnt_rate_matches_the_engine_it_watched(self):
        clock = FakeClock()
        q, _, _ = make(clock=clock, synth=self._fast_engine(clock))
        q.push(" ".join("Sentence " + "very " * i + "long." for i in range(12)))
        for _ in range(12):
            q.pump()
            q.take()
        _fixed, rtf = q._pacing._time.fit()
        assert rtf == pytest.approx(0.25, abs=0.1)    # 1s of work per 4s audio


# ── the tail ─────────────────────────────────────────────────────────────────

class TestNothingFinishesWhileItIsStillBeingMade:
    """MEASURED BUG: a continuous reply stopped 6-7 words early, with no error.

    `drain_events` ends the audio stream on `hook.finished`, which asks the
    queue whether _buffer, _pending and _chunks are all empty. pump() takes a
    sentence OUT of _pending and only puts it in _chunks once the engine has
    answered - roughly fifteen seconds later on the machine this was found on.
    For the last sentence of a reply every clause was therefore True while it
    was still being synthesised, so the drain sent voice_done and threw the
    tail away. Silently, which is the worst part: a reply that ended and a
    reply that lost its ending looked identical on the wire.
    """

    def _sampling_synth(self, box):
        """A synth that answers the finished question from INSIDE the engine
        call - the exact window the event loop was reading during."""
        def synth(text):
            while box["queue"].take() is not None:
                pass            # the player consumes as it goes
            box["seen"].append((text, box["queue"].finished))
            return {"path": "/audio/x.wav", "seconds": 2.0}
        return synth

    def test_the_last_sentence_is_not_reported_finished_mid_synthesis(self):
        box = {"queue": None, "seen": []}
        q = SpeechQueue(synth=self._sampling_synth(box), now=FakeClock())
        box["queue"] = q
        q.push("One sentence. Two sentence. The very last one.")
        q.close()
        drain(q)

        assert [text for text, _ in box["seen"]] == [
            "One sentence.", "Two sentence.", "The very last one."]
        premature = [text for text, finished in box["seen"] if finished]
        assert premature == [], f"reported finished while making {premature}"

    def test_the_reply_does_not_lose_its_last_sentence(self):
        """The user-visible shape of the bug, end to end.

        drain_events polls from the event loop while pump() runs on the
        synthesis worker, so it can read `finished` DURING an engine call.
        Standing in for that observer here: the queue is asked mid-synthesis,
        and if it says finished the drain stops - exactly what production did.
        What is left is what the listener actually heard.
        """
        box = {"queue": None, "heard": [], "stopped_early": False}

        def synth(text):
            q = box["queue"]
            while True:
                chunk = q.take()
                if chunk is None:
                    break
                box["heard"].append(chunk["text"])
            # The observer's poll lands here, inside the engine call.
            if q.finished:
                box["stopped_early"] = True
            return {"path": "/audio/x.wav", "seconds": 2.0}

        q = SpeechQueue(synth=synth, now=FakeClock())
        box["queue"] = q
        q.push("First line. Second line. Third and final line.")
        q.close()
        # Every take() in this test goes through box["heard"], including the
        # ones the synth makes: split accounting would hide a lost chunk.
        while True:
            q.pump()
            chunk = q.take()
            if chunk is None:
                break
            box["heard"].append(chunk["text"])

        assert box["stopped_early"] is False, "the drain would have cut the tail"
        assert box["heard"] == [
            "First line.", "Second line.", "Third and final line."]

    def test_a_failed_sentence_does_not_leave_the_queue_claiming_to_be_busy(self):
        """The flag is cleared on the error path too - otherwise a broken
        engine would keep `finished` False forever and the drain would spin
        until its wall-clock backstop instead of reporting the failure."""
        def boom(text):
            raise RuntimeError("engine died")

        q = SpeechQueue(synth=boom, now=FakeClock())
        q.push("Only sentence.")
        q.close()
        with pytest.raises(QueueFailed):
            q.pump()
        assert q._in_flight is False


def test_every_tag_the_queue_injects_is_one_the_budget_exempts():
    """The pairing, asserted rather than assumed.

    `narrator_tag` used to be a constructor knob nothing passed. Had anything
    passed it, the tag would have landed in the text while sanitize_for_tts was
    exempting a different string - so it would have been charged to the density
    budget and the reply would have gone plainer the longer it ran. `speech_tag`
    IS passed, from the standing tone, which is exactly why the exempt set is
    derived from the same value instead of being a constant.
    """
    import inspect

    import speech_prep

    params = inspect.signature(SpeechQueue.__init__).parameters
    assert "narrator_tag" not in params, "the knob with no source came back"

    for tone in ("deep, slow", speech_prep.DEFAULT_SPEECH_TAG):
        opts = speech_prep.PrepOptions(engine_supports_tags=True,
                                       narrative="narrator", speech_tag=tone)
        exempt = speech_prep.injected_tags(tone)
        out = speech_prep.prepare("*She waits.* Say it again.", opts)
        for tag in (opts.narrator_tag, opts.speech_tag):
            assert f"[{tag}]" in out, tag
            assert tag in exempt, f"{tag} is injected but not exempt"


def test_the_queue_is_busy_from_the_moment_it_takes_the_sentence(monkeypatch):
    """The window is popleft-to-append, not synth-to-append.

    `prepare()` runs between the two: regex passes over the sentence plus the
    user's pronunciation table. A flag raised at the engine call leaves that
    stretch uncovered, and it is the same failure - `finished` reads true while
    the last sentence of the reply is still on its way to the engine.
    """
    import speech_prep

    real_prepare = speech_prep.prepare
    seen = []

    def watched(text, opts):
        seen.append(q.finished)          # exactly where the old flag was blind
        return real_prepare(text, opts)

    monkeypatch.setattr(speech_prep, "prepare", watched)
    q, _synth, _clock = make()
    q.push("Only sentence here.")
    q.close()
    drain(q)

    assert seen == [False], "reported finished while preparing the last sentence"


class TestNoChunkOutgrowsTheBankBehindIt:
    """MEASURED BUG: the fast start was cut and then thrown away.

    `first_chunk_window` makes the opening piece small so speech can begin
    early. Nothing capped the SECOND piece, and a first chunk cannot cover a
    second that takes longer to make than the first takes to play - so
    `may_start` correctly refused and playback sat waiting for a chunk the
    early cut was supposed to have made unnecessary.

    On a real reply: 114 characters then 264, which is 7.5 s of audio against
    10.8 s of work. Playback waited 3.3 s after the first chunk was already
    made, on top of the 4.3 s it took to make it. `Pacing.max_chunk_chars` had
    been able to answer this since it was written and nothing ever asked.
    """

    #: One long narration span, the shape that produced the measurement: the
    #: sentence split lands early and leaves a much bigger remainder.
    REPLY = (
        "Your nails trailing along my throat make my whole body go tight, "
        "breath catching sharp in my chest. My eyes close completely this "
        "time, head tilting slightly into your hand without meaning to, and "
        "for a moment neither of us says anything at all, because there is "
        "nothing either of us could say that would be better than this is."
    )

    def _run(self):
        from tts import pacing as pacing_module

        pacing_module.reset_shared()
        clock = FakeClock()
        calls = []

        def synth(text):
            audio = len(text) * 0.066          # the measured seconds per char
            clock.advance(0.58 + 0.50 * audio)  # the measured c and RTF
            calls.append(len(text))
            return {"path": "/a.wav", "seconds": audio}

        q = SpeechQueue(synth=synth, now=clock,
                        pacing=pacing_module.for_model("test-model"))
        q.push(self.REPLY)
        q.close()
        drain(q)
        return q, calls

    def test_the_second_chunk_is_cut_to_what_the_first_one_covers(self):
        _q, calls = self._run()
        assert len(calls) >= 2, "the reply has to reach the engine in pieces"
        assert calls[1] <= calls[0], (
            f"chunk two ({calls[1]}) outgrew chunk one ({calls[0]}), which is "
            "exactly the shape that makes playback wait"
        )

    def test_playback_no_longer_waits_after_the_first_chunk(self):
        """The property the sizes exist to produce, asserted through the
        pacing policy itself rather than through a chunk count."""
        from tts import pacing as pacing_module

        _q, calls = self._run()
        p = pacing_module.for_model("test-model")
        banked = calls[0] * 0.066
        assert p.start_delay(banked, "x" * calls[1]) == 0.0

    def test_a_reply_that_already_fits_is_not_cut_further(self):
        """The cap only binds when it has to. Extra seams cost a fixed engine
        call each and buy nothing once the queue is ahead."""
        from tts import pacing as pacing_module

        pacing_module.reset_shared()
        clock = FakeClock()
        q, synth, _ = make(clock=clock, synth=synth_ok(seconds=2.0),
                           pacing=pacing_module.for_model("test-model"))
        q.push("One sentence. Two sentence. Three sentence.")
        q.close()
        drain(q)
        assert synth.calls == ["One sentence.", "Two sentence.",
                               "Three sentence."]
