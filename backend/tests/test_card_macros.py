"""The card macro engine, driven by the vectors both languages share.

`shared/macro_vectors.json` is loaded here and by
frontend/src/test/prompt/cardMacros.test.ts. Every vector asserts an exact
output (or the exact refusal), so a Python/TypeScript disagreement fails one
of the two suites. Nothing below retypes a vector or a constant.
"""
from __future__ import annotations

import json
import time
import tracemalloc
from pathlib import Path

import pytest

from card_macros import (
    DEFAULT_USER_NAME,
    FIELD_MAX_CODE_POINTS,
    FIELD_TOO_LARGE_CODE,
    MacroOutputTooLarge,
    render_macros,
    split_examples,
)

VECTORS_PATH = Path(__file__).resolve().parents[2] / "shared" / "macro_vectors.json"
DOC = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


def _decode(value):
    """A vector string is either a string or a list of parts, each a string or
    {repeat, times}. The same decoder exists in the TypeScript test."""
    if isinstance(value, str):
        return value
    parts = []
    for part in value:
        if isinstance(part, str):
            parts.append(part)
        else:
            parts.append(part["repeat"] * part["times"])
    return "".join(parts)


def _ctx(vec):
    ctx = dict(DOC["default_ctx"])
    for key in ("char", "user"):
        if key in vec:
            ctx[key] = _decode(vec[key])
    return ctx


RENDER = DOC["render"]
SPLIT = DOC["split"]
PIPELINE = DOC["pipeline"]


# -- the file itself ----------------------------------------------------------

def test_the_vector_file_has_ground_positive_and_refusal_cases():
    """Ground control for the whole suite: a file of vectors that all expect
    the input back would pass on an engine that does nothing."""
    assert len(RENDER) >= 25
    unchanged = [v for v in RENDER if "expect" in v and v["expect"] == v["text"]]
    changed = [v for v in RENDER if "expect" in v and v["expect"] != v["text"]]
    refused = [v for v in RENDER if "error" in v]
    assert unchanged and changed and refused
    assert len({v["id"] for v in RENDER + SPLIT + PIPELINE}) == len(RENDER + SPLIT + PIPELINE)


def test_the_module_constants_are_the_shared_constants():
    c = DOC["constants"]
    assert FIELD_MAX_CODE_POINTS == c["field_max_code_points"]
    assert DEFAULT_USER_NAME == c["default_user_name"]
    assert FIELD_TOO_LARGE_CODE == c["field_too_large_code"]
    assert MacroOutputTooLarge().code == c["field_too_large_code"]


# -- render -------------------------------------------------------------------

@pytest.mark.parametrize("vec", RENDER, ids=[v["id"] for v in RENDER])
def test_render_vector(vec):
    text = _decode(vec["text"])
    ctx = _ctx(vec)
    if "error" in vec:
        with pytest.raises(MacroOutputTooLarge) as info:
            render_macros(text, **ctx)
        assert info.value.code == vec["error"]
        assert str(info.value) == vec["error"]
    else:
        assert render_macros(text, **ctx) == _decode(vec["expect"])


def test_render_takes_names_by_keyword_only():
    """The contract signature: render_macros(text, *, char, user). A positional
    call would let a caller swap the two names without noticing."""
    with pytest.raises(TypeError):
        render_macros("{{char}}", "Alice", "Bob")  # type: ignore[misc]
    assert render_macros("{{char}}", char="Alice", user="Bob") == "Alice"


def _best_time(text, ctx, reps=5):
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        render_macros(text, **ctx)
        best = min(best, time.perf_counter() - t0)
    return best


def _growth(vec_id):
    """Render the vector's repeated unit at half its count and at its full
    count; return (full / half time, full output). The gate asserts the RATIO,
    not milliseconds: a ratio does not go red because the machine is busy,
    and a quadratic scan still shows as about 4."""
    vec = next(v for v in RENDER if v["id"] == vec_id)
    (part,) = vec["text"]
    ctx = _ctx(vec)
    half = part["repeat"] * (part["times"] // 2)
    full = part["repeat"] * part["times"]
    ratio = _best_time(full, ctx) / _best_time(half, ctx)
    return ratio, render_macros(full, **ctx), vec


def test_rendering_grows_linearly_with_the_macro_count():
    """MV18 shape (100k macros): doubling the input at most 2.5x the time."""
    ratio, out, vec = _growth("MV18")
    assert out == _decode(vec["expect"])
    assert ratio < 2.5, ratio


def test_a_refused_field_is_refused_before_it_is_built():
    """A megabyte of {{char}} with a 64-character name would be 8 MiB of
    output. The refusal must come before that is allocated, not after.

    Positive control: the same machinery, rendering an accepted field of the
    same shape, does allocate at least its own output. Without it, a low peak
    could just mean tracemalloc saw nothing."""
    name = "A" * 64
    bomb = "{{char}}" * (FIELD_MAX_CODE_POINTS // 8)
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        with pytest.raises(MacroOutputTooLarge):
            render_macros(bomb, char=name, user="Bob")
        _, refused_peak = tracemalloc.get_traced_memory()

        accepted = "{{char}}" * (FIELD_MAX_CODE_POINTS // 64)
        tracemalloc.reset_peak()
        out = render_macros(accepted, char=name, user="Bob")
        _, accepted_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(out) == FIELD_MAX_CODE_POINTS
    assert accepted_peak >= FIELD_MAX_CODE_POINTS
    assert refused_peak < FIELD_MAX_CODE_POINTS // 2, refused_peak


def test_many_unclosed_comments_stay_linear():
    """MV34 shape: 200k "{{//" with no closer. A scanner that searches for the
    closer from every opener is quadratic here (ratio about 4)."""
    ratio, out, vec = _growth("MV34")
    assert out == _decode(vec["expect"])
    assert ratio < 2.5, ratio


# -- split --------------------------------------------------------------------

@pytest.mark.parametrize("vec", SPLIT, ids=[v["id"] for v in SPLIT])
def test_split_vector(vec):
    assert split_examples(_decode(vec["text"])) == vec["expect"]


@pytest.mark.parametrize("vec", PIPELINE, ids=[v["id"] for v in PIPELINE])
def test_split_then_render(vec):
    ctx = _ctx(vec)
    blocks = [render_macros(b, **ctx) for b in split_examples(_decode(vec["text"]))]
    assert blocks == vec["expect"]


def test_render_then_split_would_have_been_wrong():
    """Positive control for PV01: the order matters, so the reverse order must
    give a different answer on the same vector."""
    vec = PIPELINE[0]
    ctx = _ctx(vec)
    wrong = split_examples(render_macros(_decode(vec["text"]), **ctx))
    assert wrong != vec["expect"]

