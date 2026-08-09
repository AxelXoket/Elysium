"""V9-4 - the two delivery dials.

`density` caps how many tags one reply may keep; `tone` is a standing direction
prepended to every spoken reply. The tone is the lever for "make the voice
deeper / slower / closer" WITHOUT hunting for a new reference clip - the clip
gives timbre, tags give performance.

The security-shaped case is the tone. It goes into square brackets, so a
closing bracket smuggled into it would END the span early and the rest of the
sentence would be read out as literal text - the exact failure the whole tag
sanitiser exists to prevent.
"""
import pytest

import voice_tags as vt


# ── the tone is cleaned, not trusted ─────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("low voice", "low voice"),
    ("  slow, intimate  ", "slow, intimate"),
    ("low] and now I am plain text", "low and now I am plain text"),
    ("multi\nline\ntone", "multi line tone"),
    ("[nested]", "nested"),
    ("", ""),
    (None, ""),
])
def test_a_tone_can_never_break_out_of_its_brackets(raw, want):
    assert vt.sanitize_tone(raw) == want


def test_a_long_tone_is_cut_not_rejected():
    out = vt.sanitize_tone("x" * 500)
    assert len(out) == vt.MAX_TONE_CHARS


# ── applying it ──────────────────────────────────────────────────────────────

def test_the_tone_leads_the_reply():
    assert vt.apply_default_tone("Hello.", "low voice",
                                 engine_supports_tags=True) == "[low voice] Hello."


def test_the_models_own_opening_tag_wins():
    # A direction the model chose for THIS line is more specific than a
    # standing default, and two stacked directions muddy both.
    text = "[whisper] Come here."
    assert vt.apply_default_tone(text, "low voice",
                                 engine_supports_tags=True) == text


def test_nothing_is_added_for_an_engine_that_would_read_brackets_aloud():
    assert vt.apply_default_tone("Hello.", "low voice",
                                 engine_supports_tags=False) == "Hello."


def test_an_empty_tone_changes_nothing():
    assert vt.apply_default_tone("Hello.", "", engine_supports_tags=True) == "Hello."


# ── density ──────────────────────────────────────────────────────────────────

def test_density_caps_tags_and_never_words():
    text = "[one tag] a [two tag] b [three tag] c"
    out = vt.sanitize_for_tts(text, engine_supports_tags=True, max_tags=1)
    assert out.count("[") == 1
    # The point of the cap: it removes DIRECTIONS, never the sentence.
    for word in ("a", "b", "c"):
        assert word in out


def test_density_zero_speaks_the_words_with_no_direction_at_all():
    out = vt.sanitize_for_tts("[soft] hello [warm] there",
                              engine_supports_tags=True, max_tags=0)
    assert "[" not in out
    assert "hello" in out and "there" in out


def test_the_default_cap_still_applies_when_none_is_passed():
    # Digit-free and all different: a digit disqualifies a span from being a
    # tag at all, and consecutive duplicates collapse before the cap is
    # reached - either would test the wrong thing.
    # Derived from the cap rather than a fixed list, so raising the ceiling
    # cannot leave this passing for the wrong reason: with fewer tags than the
    # cap every one survives and the assertion would be vacuous.
    words = [f"{chr(ord('a') + i // 26)}{chr(ord('a') + i % 26)}"
             for i in range(vt.MAX_TAGS_PER_REPLY + 5)]
    text = " ".join(f"[{w} tone] w{i}" for i, w in enumerate(words))
    out = vt.sanitize_for_tts(text, engine_supports_tags=True)
    assert out.count("[") == vt.MAX_TAGS_PER_REPLY


# ── the endpoints ────────────────────────────────────────────────────────────

def test_defaults_are_returned_before_anything_is_saved(client):
    body = client.get("/api/v1/tts/tag-prefs").json()
    assert body["density"] == vt.MAX_TAGS_PER_REPLY
    assert body["tone"] == ""
    assert body["min"] == vt.TAG_DENSITY_MIN and body["max"] == vt.TAG_DENSITY_MAX


def test_saving_round_trips(client):
    saved = client.post("/api/v1/tts/tag-prefs",
                        json={"density": 3, "tone": "low voice, slow"}).json()
    assert saved["density"] == 3 and saved["tone"] == "low voice, slow"
    assert client.get("/api/v1/tts/tag-prefs").json()["density"] == 3


def test_an_out_of_range_density_is_clamped_not_refused(client):
    assert client.post("/api/v1/tts/tag-prefs",
                       json={"density": 999}).json()["density"] == vt.TAG_DENSITY_MAX
    assert client.post("/api/v1/tts/tag-prefs",
                       json={"density": -5}).json()["density"] == vt.TAG_DENSITY_MIN


def test_a_dangerous_tone_is_stored_already_cleaned(client):
    saved = client.post("/api/v1/tts/tag-prefs",
                        json={"tone": "low] escaped"}).json()
    assert "]" not in saved["tone"]


def test_updating_one_dial_leaves_the_other_alone(client):
    client.post("/api/v1/tts/tag-prefs", json={"density": 2, "tone": "warm"})
    client.post("/api/v1/tts/tag-prefs", json={"density": 5})
    body = client.get("/api/v1/tts/tag-prefs").json()
    assert body["density"] == 5 and body["tone"] == "warm"


# ── Audit: the reading-speed dial was advertised but never wired ────────────
#
# matrix.APP_LEVEL declares `speed` "applied by Elysium and works the same on
# every voice model", and describe() removes the engine's own rate knob from the
# panel on that promise. Nothing read or wrote SETTING_SPEED, the frontend never
# sent a rate, and make_stream_synth passed rate=None into speed.engine_values -
# whose clamp turns None into 1.0 and merged THAT over the model's saved value.
# So the dial did not exist, and a saved speed was honoured on replay but
# overridden live: the same reply paced two different ways.


def test_tag_prefs_exposes_the_speed_dial(client):
    from tts import speed

    body = client.get("/api/v1/tts/tag-prefs").json()
    assert body["speed"] == speed.DEFAULT_RATE
    assert body["speed_min"] == speed.MIN_RATE
    assert body["speed_max"] == speed.MAX_RATE


def test_saving_the_speed_dial_round_trips(client):
    resp = client.post("/api/v1/tts/tag-prefs", json={"speed": 1.25})
    assert resp.status_code == 200, resp.text
    assert resp.json()["speed"] == 1.25
    assert client.get("/api/v1/tts/tag-prefs").json()["speed"] == 1.25


def test_an_out_of_range_speed_is_clamped_not_rejected(client):
    from tts import speed

    assert client.post(
        "/api/v1/tts/tag-prefs", json={"speed": 9.0},
    ).json()["speed"] == speed.MAX_RATE
    assert client.post(
        "/api/v1/tts/tag-prefs", json={"speed": 0.01},
    ).json()["speed"] == speed.MIN_RATE


def test_saving_only_the_tone_leaves_the_speed_alone(client):
    client.post("/api/v1/tts/tag-prefs", json={"speed": 0.9})
    client.post("/api/v1/tts/tag-prefs", json={"tone": "low voice"})
    assert client.get("/api/v1/tts/tag-prefs").json()["speed"] == 0.9


def test_stored_rate_survives_a_malformed_setting(client):
    """Two comfort dials must never be able to cost somebody their audio."""
    import database
    import routers.tts_runtime as runtime
    import voice_tags

    database.set_setting(voice_tags.SETTING_SPEED, "not a number")
    assert runtime._stored_rate() is None
    database.set_setting(voice_tags.SETTING_SPEED, "")
    assert runtime._stored_rate() is None
    database.set_setting(voice_tags.SETTING_SPEED, "1.15")
    assert runtime._stored_rate() == 1.15
