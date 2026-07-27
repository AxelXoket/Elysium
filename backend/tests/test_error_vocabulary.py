"""Audit KÖK 14: the map that was synced against a stale document.

errorMessages.ts opens by declaring that every code in the contract is mapped
here, and treats that document as the source of truth. It was wrong in both
directions at once - four vault/CSRF codes the routers raise had no entry and
fell through to "Something went wrong. Please try again.", while a code the
contract listed had no entry either.

One root: the map was checked against a document, and the document was checked
against nobody. The ROUTERS are the only thing that actually decides which
codes a user can receive, so that is what these read.

tts/errors.py has had this guard for its own vocabulary since it was written
(test_tts_contract.py). This is the same guard for everything else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
MESSAGES = (
    BACKEND.parent / "frontend" / "src" / "lib" / "errors" / "errorMessages.ts"
)

#: `raise HTTPException(422, "passphrase_too_long")` - the second argument IS
#: the code the client receives. Only literals: a computed detail cannot be
#: checked from here, and every one in this codebase is a literal today.
_RAISE = re.compile(r'HTTPException\(\s*\d{3}\s*,\s*"([a-z0-9_]+)"')
#: The other shape, used by the middleware layer.
_JSON = re.compile(r'JSONResponse\(\s*[^)]*?"detail"\s*:\s*"([a-z0-9_]+)"')

#: Not user-facing vocabulary: FastAPI's own validation shape, and the codes
#: the frontend deliberately handles structurally rather than by sentence.
_NOT_A_MESSAGE = frozenset({"invalid_generation_params"})


def _source_files() -> list[Path]:
    skip = {"tests", "verify", ".venv", "__pycache__"}
    return [
        p for p in BACKEND.rglob("*.py")
        if not skip & set(p.relative_to(BACKEND).parts)
    ]


def _raised_codes() -> set[str]:
    codes: set[str] = set()
    for path in _source_files():
        body = path.read_text(encoding="utf-8")
        codes |= set(_RAISE.findall(body))
        codes |= set(_JSON.findall(body))
    return codes - _NOT_A_MESSAGE


def _mapped(body: str, code: str) -> bool:
    return f"{code}:" in body


def test_every_code_a_router_can_raise_has_a_sentence():
    """The invariant errorMessages.ts states about itself, enforced.

    A missing entry is not a missing nicety: it is the difference between "That
    passphrase is too long. Use 1024 characters or fewer." and "Something went
    wrong. Please try again." - and the second one makes the user paste the
    identical passphrase again, forever.
    """
    if not MESSAGES.is_file():
        pytest.skip("errorMessages.ts is not present in this checkout")
    body = MESSAGES.read_text(encoding="utf-8")
    missing = sorted(c for c in _raised_codes() if not _mapped(body, c))
    assert not missing, (
        "codes a router raises with no human sentence: "
        f"{missing} - add them to frontend errorMessages.ts"
    )


def test_the_scan_actually_finds_the_routers():
    """A regex that quietly matched nothing would make the test above pass
    forever while proving nothing at all."""
    codes = _raised_codes()
    assert len(codes) > 30, f"only found {len(codes)} codes - the scan is broken"
    # Spot-checks across three different routers, so a change that stops the
    # walk reaching one of them is caught rather than silently narrowing it.
    for expected in ("chat_not_found", "api_key_missing", "passphrase_too_long"):
        assert expected in codes, expected


def test_the_vault_codes_that_were_missing_are_mapped_now():
    """Named individually because they are the reported ones: all four were
    reachable, and all four rendered as the generic fallback."""
    body = MESSAGES.read_text(encoding="utf-8")
    for code in (
        "passphrase_too_long",
        "vault_already_initialized",
        "vault_not_initialized",
        "cross_origin_denied",
    ):
        assert _mapped(body, code), code
