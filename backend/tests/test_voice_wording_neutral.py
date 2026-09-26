"""A8 (Ek O 9 c): the default voice block carries neutral wording.

The block rides on every request while voice is on, so its example lines and
tone list are an instruction the app gives on the user's behalf. The
neutral-base rule removed `seductive`, `breathless`, `husky`, `intimacy` and
the "[seductive] I missed you" example. A user who wants those tones can still
write them into a card or a message; the app just does not suggest them.

Driven through `voice_block()` with voice switched on and a tag-reading engine
selected, which is the string the chat route injects.
"""
from __future__ import annotations

import re

import voice_tags

from tests.test_voice_tags_wiring import _enable_voice

REMOVED = ("seductive", "breathless", "husky", "intimacy", "intimate")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def test_ground_the_block_is_really_injected(client, monkeypatch, tmp_path):
    _enable_voice(client, monkeypatch, tmp_path)
    block = voice_tags.voice_block()
    assert block == voice_tags.VOICE_PROMPT
    assert "[whisper]" in block


def test_the_removed_wording_is_gone(client, monkeypatch, tmp_path):
    _enable_voice(client, monkeypatch, tmp_path)
    block = voice_tags.voice_block()
    assert block, "ground: an empty block would pass the absence below"
    assert not (_words(block) & set(REMOVED))


def test_positive_control_the_neutral_tones_took_their_place(
        client, monkeypatch, tmp_path):
    _enable_voice(client, monkeypatch, tmp_path)
    block = voice_tags.voice_block()
    for tone in ("[gentle]", "[hesitant]", "[serious]"):
        assert tone in block
    # The worked example that replaced the old first line still shows a tone
    # tag at the start of a sentence and a second one mid-line.
    first = voice_tags.PROMPT_EXAMPLE_LINES[0]
    assert first.startswith("[warm] ") and "[low voice]" in first


def test_every_example_still_survives_the_pipeline():
    """The replacement lines must strip and speak as cleanly as the old."""
    for line in voice_tags.PROMPT_EXAMPLE_LINES:
        assert "[" not in voice_tags.strip_tags(line)
        assert voice_tags.sanitize_for_tts(line, engine_supports_tags=True)
