"""XTTS long text is split by the worker, never by the engine.

Coqui's own splitter passes text under 250 characters through and, at or past
that mark, asks spacy for a sentence pipeline. spacy is not in the freeze and
never was in the environment that was measured, so a single sentence of 250
characters or more raised ImportError inside the engine and the app answered
tts_synthesis_failed. Measured 8 September 2026 on a 299-character sentence
through the real installer, real model and real GPU (verify_tts_install.py).

The worker now cuts the text itself into pieces of at most 250 characters,
hands the engine one piece at a time with its own splitter switched off, and
joins the audio. Loaded by path, like the other worker tests, because the
worker may import nothing from the app.
"""
from __future__ import annotations

import importlib.util
import sys
import wave
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[1] / "tts" / "worker" / "xtts_v2.py"


def _load():
    spec = importlib.util.spec_from_file_location("xtts_split_probe", WORKER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["xtts_split_probe"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop("xtts_split_probe", None)
    return mod


@pytest.fixture(scope="module")
def worker():
    return _load()


LONG = ("This is a deliberately long single sentence, written without a full stop "
        "anywhere inside it, so that it runs past the two hundred and fifty "
        "character mark that one of the engines uses as its own splitting "
        "threshold, and thereby shows whether a long sentence is spoken or refused "
        "by the engine today.")


class TestSplitText:
    def test_short_text_is_one_piece(self, worker):
        assert worker.split_text("Hello there.") == ["Hello there."]

    def test_the_measured_sentence_comes_back_in_pieces_under_the_limit(self, worker):
        assert len(LONG) > worker.SPLIT_CHAR_LIMIT, "ground: the input is over the limit"
        pieces = worker.split_text(LONG)
        assert len(pieces) >= 2
        assert all(0 < len(p) <= worker.SPLIT_CHAR_LIMIT for p in pieces), pieces
        assert " ".join(pieces) == LONG, "nothing dropped, nothing added"

    def test_sentence_ends_are_preferred_to_clause_marks(self, worker):
        a = "First sentence, with a clause inside it that goes on for a while yet."
        b = "Second sentence, also with a clause, long enough to matter here today."
        c = "Third sentence closes the paragraph with a full stop at its end."
        text = f"{a} {b} {c}"
        pieces = worker.split_text(text, limit=len(a) + 1 + len(b))
        assert pieces == [f"{a} {b}", c], pieces

    def test_a_sentence_past_the_limit_is_cut_at_a_clause_then_at_spaces(self, worker):
        clause = "one two three four five six seven eight nine ten"
        text = f"{clause}, {clause}, {clause}"
        pieces = worker.split_text(text, limit=len(clause) + 1)
        assert pieces == [f"{clause},", f"{clause},", clause]
        words = worker.split_text("alpha beta gamma delta", limit=11)
        assert words == ["alpha beta", "gamma delta"]

    def test_a_word_longer_than_the_limit_is_cut_hard(self, worker):
        pieces = worker.split_text("x" * 23, limit=10)
        assert pieces == ["x" * 10, "x" * 10, "xxx"]

    def test_whitespace_runs_collapse_and_empty_text_is_no_piece(self, worker):
        assert worker.split_text("  a   b \n c  ") == ["a b c"]
        assert worker.split_text("   ") == []

    def test_the_limit_is_the_engines_own(self, worker):
        assert worker.SPLIT_CHAR_LIMIT == 250


class _Model:
    """Records what the engine was handed; answers a short flat wave."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    # Named, as on the real Xtts.inference: the worker forwards only the
    # kwargs the engine's signature names (_split_supported), so a fake that
    # took **kw alone would never be handed the switch this test reads.
    def inference(self, text, language, gpt_cond_latent, speaker_embedding,
                  enable_text_splitting=True, **kw):
        self.calls.append((text, {"enable_text_splitting": enable_text_splitting, **kw}))
        return {"wav": [0.25] * 4}


class TestSynthesisUsesThePieces:
    def _drive(self, worker, monkeypatch, tmp_path, text, *, split=True):
        model = _Model()
        monkeypatch.setattr(worker, "_require_model", lambda: model)
        monkeypatch.setattr(worker, "_conditioning", lambda values, req: (None, None, "stub"))
        monkeypatch.setattr(worker, "_language", lambda values, req: "en")
        monkeypatch.setitem(worker.STATE, "sample_rate", 24000)
        out = tmp_path / "out.wav"
        events = []
        result = worker._op_synthesize(
            {"text": text, "out": str(out), "values": {"enable_text_splitting": split}},
            events.append,
        )
        return model, out, result, events

    def test_a_long_sentence_reaches_the_engine_as_pieces_with_its_splitter_off(
            self, worker, monkeypatch, tmp_path):
        model, out, result, _ = self._drive(worker, monkeypatch, tmp_path, LONG)
        texts = [t for t, _ in model.calls]
        assert texts == worker.split_text(LONG)
        assert len(texts) >= 2
        for _, kw in model.calls:
            assert kw["enable_text_splitting"] is False, \
                "the engine's own splitter would ask for spacy"
        with wave.open(str(out), "rb") as fh:
            assert fh.getnframes() == 4 * len(texts), "every piece's audio is kept, in order"
        assert result["seconds"] == pytest.approx(4 * len(texts) / 24000, abs=1e-3)

    def test_a_short_sentence_is_one_call_and_the_splitter_is_still_off(
            self, worker, monkeypatch, tmp_path):
        model, out, _, _ = self._drive(worker, monkeypatch, tmp_path, "Hello there.")
        assert [t for t, _ in model.calls] == ["Hello there."]
        assert model.calls[0][1]["enable_text_splitting"] is False

    def test_with_splitting_off_a_long_sentence_goes_whole(self, worker, monkeypatch, tmp_path):
        """Ground control: the setting still means something, the user can
        turn the cutting off and the engine gets the text in one piece."""
        model, _, _, _ = self._drive(worker, monkeypatch, tmp_path, LONG, split=False)
        assert [t for t, _ in model.calls] == [LONG]

    def test_progress_walks_the_pieces(self, worker, monkeypatch, tmp_path):
        _, _, _, events = self._drive(worker, monkeypatch, tmp_path, LONG)
        pcts = [e["pct"] for e in events if e.get("stage") == "synthesizing"]
        assert len(pcts) == len(worker.split_text(LONG))
        assert pcts == sorted(pcts) and pcts[0] == 0.35
