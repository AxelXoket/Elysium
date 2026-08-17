"""Two architectural boundaries that only a reading of the source can state.

These are the last of the P-01..P-20 privacy checks from
`verify/verify_elysium_full.py`, which is being deleted. Thirteen of the twenty
were already covered by behavioural tests, and better than the greps were -
`test_privacy_promises.py` catches the actual bytes leaving for the provider
rather than the spelling of a constant. Five more moved to behavioural tests
alongside them. What is left here are the two that are not statements about
behaviour at all.

"image_url is built in exactly one module" and "stream: true is set in exactly
one module" are claims about SHAPE. A running request cannot answer them: a
second builder somewhere else would produce a perfectly valid payload, and
every behavioural assertion would stay green while the attachment gate and the
provider policy assembled around the first builder were quietly bypassed. The
question is how many places exist, and the only place that is written down is
the source.

That makes this the same kind of check as
`test_egress_chokepoint.py::TestNothingElseBuildsAClient`, and it is built the
same way, to the same four rules: a floor under how much was walked, exclusions
that are named and justified rather than assumed, a positive control that the
pattern matches what it forbids, and a discriminating control that it does not
match the innocent line next door.

WHAT THIS CANNOT DO, because a green run should not be read as more than it is:
it reads text. `payload["str" + "eam"] = True` defeats it, and so does a field
assembled at runtime from a variable. That is the ceiling of static reading and
it is not patchable here. These exist to stop a SECOND obvious construction
site appearing, which is the mistake somebody actually makes; the payload
itself is asserted on, byte for byte, in test_privacy_promises.py.

AND ONE CHECK IS DELIBERATELY NOT PORTED. P-16 grepped backend source for
`localStorage`, `sessionStorage` and `IndexedDB`. Measured on 2026-08-17: all
six occurrences are prose, in comments explaining what the FRONTEND must not
do, and they are the reason the original had to carry a comment-stripper. The
check is a category error - those names in a Python file are a NameError, not a
privacy leak - and the rule it stands for is enforced where it applies, by
eslint.config.js (S-04, S-09, S-13, S-17) and by static-safety.test.ts, both of
which read the code that actually runs in a browser. Dropped on purpose, said
out loud rather than left to be discovered as an omission.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

#: (id, what it protects, pattern, files allowed to match, lines allowed to
#: match, a line that MUST match, a line that must NOT).
BOUNDARIES = (
    (
        "P-03",
        "an outbound image part is built in one place, so the attachment gate "
        "and the model-capability rule cannot be walked around",
        r'["\']image_url["\']',
        {"attachments_service.py"},
        # openrouter.image_urls_from READS what the provider sent back. That is
        # the opposite direction and it has to be read somewhere; the exemption
        # is one line rather than the whole file so a new outbound builder in
        # openrouter.py is still visible.
        ('entry.get("image_url")',),
        '"image_url": {"url": data_uri},',
        'parts.append({"type": "text", "text": body})',
    ),
    (
        "P-07",
        "the streaming request is constructed in one place, so no path can "
        "skip the provider policy assembled around it",
        r'"stream"\s*:\s*[Tt]rue',
        {"openrouter.py"},
        (),
        'body = {"stream": True}',
        'if payload.get("stream"):',
    ),
)

#: Directories that are not the shipped application.
_NOT_APP = {"tests", "verify", ".venv", "__pycache__", "build", "dist"}


def app_modules() -> list[Path]:
    backend = Path(__file__).resolve().parents[1]
    return [
        p for p in backend.rglob("*.py")
        if not (_NOT_APP & set(p.relative_to(backend).parts))
    ]


@pytest.mark.parametrize("pid,what,pattern,allowed,allowed_lines,offender,clean",
                         BOUNDARIES)
def test_the_boundary_holds(
    pid: str, what: str, pattern: str, allowed: set[str],
    allowed_lines: tuple[str, ...], offender: str, clean: str,
) -> None:
    modules = app_modules()
    # Floor: an empty walk reports perfect compliance.
    assert len(modules) >= 40, f"only {len(modules)} modules walked"

    door = re.compile(pattern)
    strays = [
        f"{p.name}:{i}"
        for p in modules
        if p.name not in allowed
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if door.search(line)
        and not any(frag in line for frag in allowed_lines)
    ]
    assert not strays, f"{pid}: {what}. Second construction site at {strays}"


@pytest.mark.parametrize("pid,what,pattern,allowed,allowed_lines,offender,clean",
                         BOUNDARIES)
def test_the_boundary_is_not_watching_an_empty_room(
    pid: str, what: str, pattern: str, allowed: set[str],
    allowed_lines: tuple[str, ...], offender: str, clean: str,
) -> None:
    """The allowed file must still contain the thing it is allowed to contain.

    This is the half the original grep did not have, and it is the one that
    matters most for a confinement rule: if the single builder is renamed,
    moved or deleted, "nobody else does it" becomes true for the wrong reason
    and the check goes green on a feature that no longer exists.
    """
    modules = {p.name: p for p in app_modules()}
    door = re.compile(pattern)
    for name in allowed:
        assert name in modules, f"{pid}: {name} is gone"
        assert door.search(modules[name].read_text(encoding="utf-8")), (
            f"{pid}: {name} no longer contains what it is the one place for, "
            f"so this boundary is guarding nothing"
        )


@pytest.mark.parametrize("pid,what,pattern,allowed,allowed_lines,offender,clean",
                         BOUNDARIES)
def test_no_line_is_excused_that_is_no_longer_there(
    pid: str, what: str, pattern: str, allowed: set[str],
    allowed_lines: tuple[str, ...], offender: str, clean: str,
) -> None:
    """A stale exemption excuses nothing and hides that it excuses nothing.

    Worse than that: the fragment is a substring test, so an exemption written
    for one line silently covers any future line that contains it. Requiring
    it to still match something keeps it attached to the code it was granted
    for.
    """
    text = "\n".join(p.read_text(encoding="utf-8") for p in app_modules())
    for fragment in allowed_lines:
        assert fragment in text, (
            f"{pid} excuses {fragment!r}, which no longer appears anywhere")


@pytest.mark.parametrize("pid,what,pattern,allowed,allowed_lines,offender,clean",
                         BOUNDARIES)
def test_the_pattern_can_actually_fail(
    pid: str, what: str, pattern: str, allowed: set[str],
    allowed_lines: tuple[str, ...], offender: str, clean: str,
) -> None:
    door = re.compile(pattern)
    assert door.search(offender), f"{pid} would not notice its own offender"
    assert not door.search(clean), f"{pid} fires on an innocent neighbour"
