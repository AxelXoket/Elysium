"""V8-5 - the bridge between the speech queue and a real engine.

`make_stream_synth` is the ONE place words become audio for a live reply, and
it has to behave identically to `/speak`: same model resolution, same
vault-stored settings, same reference voice, same tag policy. Two code paths
would drift and the drift would be audible - the same sentence in two different
voices depending on whether it was spoken live or replayed.

Everything below is a unit test with the engine stubbed out. What is being
asserted is the DECISIONS the bridge makes, which is exactly the part that can
be wrong without any GPU noticing.
"""
import pytest

import routers.tts_runtime as rt
from tts import speed
from tts.base import ParamSpec


class FakeAdapter:
    def __init__(self, tags=True, specs=None):
        self.capabilities = type("Caps", (), {"inline_prosody_tags": tags})()
        self._specs = specs or []

    def describe_settings(self, model):
        return self._specs


class FakeHost:
    def __init__(self, uid="m1"):
        self.uid = uid
        self.loads = 0
        self.calls = []

    def snapshot(self):
        return {"state": "loaded", "uid": self.uid}

    def load(self, model, values):
        self.loads += 1

    def speak(self, text, values, extra=None):
        self.calls.append({"text": text, "values": values, "extra": extra or {}})
        return {"path": f"C:/cache/abc{len(self.calls)}.wav",
                "seconds": 1.25, "sample_rate": 44100}


@pytest.fixture
def bridge(monkeypatch):
    """Stub the engine side; keep the bridge's own logic real."""
    model = type("Model", (), {"uid": "m1", "engine_id": "fish_s2"})()
    host = FakeHost()

    def install(*, tags=True, specs=None):
        monkeypatch.setattr(rt, "_resolve", lambda uid=None: model)
        monkeypatch.setattr(rt, "adapter_for",
                            lambda engine_id: FakeAdapter(tags, specs))
        monkeypatch.setattr(rt, "_values_for", lambda m: {"temperature": 0.7})
        monkeypatch.setattr(rt, "_expand_reference",
                            lambda m, v: (v, {"reference": {"path": "r.wav"}}))
        monkeypatch.setattr(rt, "_host", lambda: host)
        return host

    return install


# ── parity with /speak ───────────────────────────────────────────────────────

def test_tags_are_kept_for_a_tag_capable_engine(bridge):
    host = bridge(tags=True)
    synth = rt.make_stream_synth(rate=1.0)
    synth("[soft, close] Come here.")
    assert "[soft, close]" in host.calls[0]["text"]


def test_tags_are_stripped_for_an_engine_that_would_read_them_aloud(bridge):
    host = bridge(tags=False)
    synth = rt.make_stream_synth(rate=1.0)
    synth("[whisper] Come here.")
    spoken = host.calls[0]["text"]
    assert "[" not in spoken and "whisper" not in spoken
    assert "Come here." in spoken


def test_the_reference_voice_travels_with_every_sentence(bridge):
    host = bridge()
    synth = rt.make_stream_synth(rate=1.0)
    synth("One.")
    synth("Two.")
    # Same conditioning on every chunk: a reference that applied only to the
    # first sentence would make the voice drift mid-paragraph.
    assert host.calls[0]["extra"]["reference"] == host.calls[1]["extra"]["reference"]


def test_the_model_is_resolved_once_not_per_sentence(bridge, monkeypatch):
    bridge()
    resolved = []
    real = rt._resolve
    monkeypatch.setattr(rt, "_resolve",
                        lambda uid=None: (resolved.append(uid), real(uid))[1])
    synth = rt.make_stream_synth(rate=1.0)
    synth("One.")
    synth("Two.")
    # Swapping models halfway through a reply would change voice mid-paragraph,
    # which is worse than finishing in the voice that started.
    assert len(resolved) == 1


def test_text_that_sanitises_to_nothing_is_refused_not_synthesised(bridge):
    host = bridge(tags=False)
    synth = rt.make_stream_synth(rate=1.0)
    with pytest.raises(ValueError):
        synth("[whisper]")
    assert host.calls == []


# ── the reading-speed dial reaching the worker ───────────────────────────────

def test_a_dsp_rate_is_sent_to_the_worker(bridge):
    host = bridge(specs=[])                       # no native speed knob
    synth = rt.make_stream_synth(rate=1.2)
    synth("One.")
    assert host.calls[0]["extra"]["rate"] == pytest.approx(1.2)


def test_a_rate_of_one_is_not_sent_at_all(bridge):
    host = bridge(specs=[])
    synth = rt.make_stream_synth(rate=1.0)
    synth("One.")
    assert "rate" not in host.calls[0]["extra"]


def test_a_sub_audible_rate_is_not_sent_either(bridge):
    host = bridge(specs=[])
    synth = rt.make_stream_synth(rate=1.01)
    synth("One.")
    assert "rate" not in host.calls[0]["extra"]


def test_an_engine_with_its_own_speed_gets_the_value_and_no_dsp(bridge):
    specs = [ParamSpec("speed", "float", 1.0, "Speed", minimum=0.5, maximum=1.5)]
    host = bridge(specs=specs)
    synth = rt.make_stream_synth(rate=1.2)
    synth("One.")
    assert host.calls[0]["values"]["speed"] == pytest.approx(1.2)
    assert "rate" not in host.calls[0]["extra"]


def test_an_out_of_range_rate_is_clamped_before_it_leaves(bridge):
    host = bridge(specs=[])
    synth = rt.make_stream_synth(rate=9.0)
    synth("One.")
    assert host.calls[0]["extra"]["rate"] == pytest.approx(speed.MAX_RATE)


# ── what the queue needs back ────────────────────────────────────────────────

def test_the_result_carries_what_the_client_needs_to_play_it(bridge):
    bridge()
    synth = rt.make_stream_synth(rate=1.0)
    out = synth("One.")
    assert out["audio_id"] == "abc1"          # /tts/audio/{audio_id} serves it
    assert out["seconds"] == pytest.approx(1.25)
    assert out["sample_rate"] == 44100


def test_a_model_that_is_not_loaded_is_loaded_once(bridge):
    host = bridge()
    host.uid = "someone-else"
    synth = rt.make_stream_synth(rate=1.0)
    synth("One.")
    assert host.loads == 1


# ── audit regressions (2026-07-25 whole-repo audit) ──────────────────────────

def test_the_live_path_applies_the_delivery_dials(bridge, monkeypatch):
    """Regression: make_stream_synth never read the density cap or the standing
    tone, so both Delivery dials applied ONLY to replay - the same sentence
    sounded different live than when the Speak button repeated it."""
    host = bridge(tags=True)
    monkeypatch.setattr(rt, "_tag_prefs", lambda: (1, "low voice"))
    synth = rt.make_stream_synth(rate=1.0)
    # Opens with WORDS, not a tag: a reply whose own first token is a direction
    # deliberately keeps it instead of being prefixed twice.
    synth("a [one tag] b [two tag] c [three tag] d")
    spoken = host.calls[0]["text"]
    assert spoken.startswith("[low voice]")     # the standing tone leads
    assert spoken.count("[") == 2               # tone + exactly one kept tag
    for word in ("a", "b", "c", "d"):
        assert word in spoken                   # the cap never eats words


def test_a_reply_that_opens_with_its_own_tag_is_not_prefixed_twice(bridge,
                                                                   monkeypatch):
    host = bridge(tags=True)
    monkeypatch.setattr(rt, "_tag_prefs", lambda: (8, "low voice"))
    rt.make_stream_synth(rate=1.0)("[whisper] Come here.")
    # The model's choice for THIS line is more specific than a standing default.
    assert host.calls[0]["text"].startswith("[whisper]")


def test_the_live_path_leaves_the_tone_off_when_none_is_set(bridge, monkeypatch):
    host = bridge(tags=True)
    monkeypatch.setattr(rt, "_tag_prefs", lambda: (8, ""))
    rt.make_stream_synth(rate=1.0)("Plain sentence.")
    assert host.calls[0]["text"] == "Plain sentence."
