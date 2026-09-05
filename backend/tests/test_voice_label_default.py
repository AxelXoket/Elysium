"""What a reference voice is called when nobody said - and the clip that used
to be destroyed by the answer.

THE DEFECT, and it was live: an upload with no name was stored under the
opaque voice id, and the panel in front of it made up the fixed string
"Untitled voice" to spare the reader that. The panel decides a collision by
comparing LABELS case-insensitively and, on a match, sends the new clip under
the EXISTING voice's id - which is a replace. So two unnamed uploads produced
the same made-up label, the second matched the first, and it overwrote the
first clip AND the transcript the user had corrected by hand. No warning,
because from the panel's side it looked like a deliberate replace.

A numbered name cannot collide with the one before it, so the second upload is
a second voice. That is what the first test below measures, at the layer where
the bytes actually are: two files, two folders, two transcripts, both intact.
"""
from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

import config
import default_names
from test_tts_runtime_api import voice  # noqa: F401
from tts import refs


@pytest.fixture
def refs_root(monkeypatch, tmp_path):
    root = tmp_path / "voice" / "refs"
    root.mkdir(parents=True)
    monkeypatch.setattr(config, "TTS_REFS_DIR", str(root), raising=False)
    return root


def _wav_bytes(seconds: float = 8.0, rate: int = 44100) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def _labels(root: Path) -> list[str]:
    return sorted(v.label for v in refs.list_voices())


def test_an_unnamed_upload_is_numbered_not_left_as_an_id(refs_root):
    saved = refs.save_upload("voice-a", "take.wav", _wav_bytes())

    assert saved.label == f"{default_names.VOICE} 1"
    assert saved.label != saved.voice_id


def test_a_second_unnamed_upload_does_not_overwrite_the_first(refs_root):
    """The data-loss case, measured on the disk rather than on the label.

    Both clips have to survive as separate voices, and the transcript the
    first upload carried has to still belong to the first clip. Under the old
    naming both uploads landed in one folder and the second transcript
    replaced the first.
    """
    first = refs.save_upload("voice-a", "one.wav", _wav_bytes(),
                             transcript="the words of the first take")
    second = refs.save_upload("voice-b", "two.wav", _wav_bytes(),
                              transcript="the words of the second take")

    assert first.voice_id != second.voice_id
    assert first.label != second.label
    assert {first.label, second.label} == {
        f"{default_names.VOICE} 1", f"{default_names.VOICE} 2"}

    # Two voices, two folders, and the first one's words are still its own.
    listed = refs.list_voices()
    assert len(listed) == 2, [v.label for v in listed]
    assert Path(first.path) != Path(second.path)
    assert refs.describe("voice-a").transcript == "the words of the first take"
    assert refs.describe("voice-b").transcript == "the words of the second take"


def test_a_label_of_only_spaces_is_treated_as_no_label(refs_root):
    """It used to be stored verbatim, so the panel listed a voice whose name
    was invisible."""
    saved = refs.save_upload("voice-a", "take.wav", _wav_bytes(), label="   ")

    assert saved.label == f"{default_names.VOICE} 1"


def test_the_sequence_continues_across_uploads(refs_root):
    refs.save_upload("voice-a", "a.wav", _wav_bytes())
    refs.save_upload("voice-b", "b.wav", _wav_bytes())
    third = refs.save_upload("voice-c", "c.wav", _wav_bytes())

    assert third.label == f"{default_names.VOICE} 3"


def test_a_deleted_number_is_handed_out_again(refs_root):
    refs.save_upload("voice-a", "a.wav", _wav_bytes())
    refs.save_upload("voice-b", "b.wav", _wav_bytes())
    refs.save_upload("voice-c", "c.wav", _wav_bytes())

    assert refs.delete("voice-b") is True

    fourth = refs.save_upload("voice-d", "d.wav", _wav_bytes())
    assert fourth.label == f"{default_names.VOICE} 2"


def test_replacing_a_voice_without_a_name_keeps_its_number(refs_root):
    """A replace excludes the voice being replaced from the scan: its label is
    on its way out, so it must not push its own replacement along."""
    refs.save_upload("voice-a", "a.wav", _wav_bytes())

    again = refs.save_upload("voice-a", "a2.wav", _wav_bytes())

    assert again.label == f"{default_names.VOICE} 1"
    assert len(refs.list_voices()) == 1


def test_a_hand_typed_number_is_not_walked_over(refs_root):
    refs.save_upload("voice-a", "a.wav", _wav_bytes(),
                     label=f"{default_names.VOICE} 5")

    second = refs.save_upload("voice-b", "b.wav", _wav_bytes())

    assert second.label == f"{default_names.VOICE} 1"


def test_a_differently_cased_label_still_holds_its_number(refs_root):
    refs.save_upload("voice-a", "a.wav", _wav_bytes(), label="new voice 1")

    second = refs.save_upload("voice-b", "b.wav", _wav_bytes())

    assert second.label == f"{default_names.VOICE} 2"


def test_a_label_that_was_given_is_stored_exactly(refs_root):
    """GROUND CONTROL. Everything above is about the empty box."""
    saved = refs.save_upload("voice-a", "a.wav", _wav_bytes(), label="Anna")

    assert saved.label == "Anna"
    assert refs.describe("voice-a").label == "Anna"
    assert _labels(refs_root) == ["Anna"]


def test_the_route_names_an_upload_that_carries_no_label_field(
        client, voice, monkeypatch, tmp_path):  # noqa: F811
    """The wiring, over the wire.

    Everything above drives `refs` directly. The route declares
    `label: str = Form("")`, so a form with no `label` part at all - which is
    exactly what the panel sends for an unnamed clip, because the transport
    leaves an empty field out - has to arrive here as an empty label and come
    back numbered. Without this the default could be correct in `refs` and
    still unreachable from the browser.
    """
    root = tmp_path / "route" / "refs"
    root.mkdir(parents=True)
    monkeypatch.setattr(config, "TTS_REFS_DIR", str(root), raising=False)

    first = client.post(
        "/api/v1/tts/voices/one",
        files={"file": ("ref.wav", _wav_bytes(), "audio/wav")},
    )
    second = client.post(
        "/api/v1/tts/voices/two",
        files={"file": ("ref.wav", _wav_bytes(), "audio/wav")},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["label"] == f"{default_names.VOICE} 1"
    assert second.json()["label"] == f"{default_names.VOICE} 2"

    listing = client.get("/api/v1/tts/voices").json()["voices"]
    assert sorted(v["voice_id"] for v in listing) == ["one", "two"]


def test_unrelated_labels_do_not_advance_the_sequence(refs_root):
    """POSITIVE CONTROL."""
    refs.save_upload("voice-a", "a.wav", _wav_bytes(), label="Anna")
    refs.save_upload("voice-b", "b.wav", _wav_bytes(), label="a night voice")

    third = refs.save_upload("voice-c", "c.wav", _wav_bytes())

    assert third.label == f"{default_names.VOICE} 1"
