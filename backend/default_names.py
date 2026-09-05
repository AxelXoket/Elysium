"""default_names.py -- one answer to "what is this called when nobody said".

Every surface that stores a name a person reads on screen used to answer the
empty box differently, and all five answers were wrong in a different way:

    chats.py        stamped `<character> - <UTC minute>`, so three chats
                    opened in the same minute were byte-for-byte identical
    characters.py   refused with 422, so the box could not be left alone
    personas.py     refused with 422
    settings.py     DELETED the stored proxy label and answered {"ok": true}
    tts/refs.py     stored the opaque voice id as the label, and the panel in
                    front of it fell back to a fixed "Untitled voice" string
                    that made two unnamed uploads collide - the second one
                    overwrote the first clip AND its hand-corrected words

So the rule is one rule, in one place, on the SERVER side of every one of
those doors. The panels in front of them are not the guard: POST /characters
answered 422 to anyone who called it directly, whatever the dialog did, and
three separate fallback-name expressions in the frontend disagreed with each
other about what the fallback even was.

WHAT THE GENERATED NAME IS

`<prefix> <n>`, where n is the SMALLEST FREE NUMBER, not one past the
largest. Nothing is stored to make that work: the answer is derived from the
list that exists at the moment of the write, so there is no counter column,
no migration, and a vault restored from an export still numbers correctly.
It also means a deleted "New Chat 2" is available again, which is the
behaviour a person watching a sidebar expects from a name that reads like a
slot.

WHAT THE SCAN COSTS

It is linear in the number of rows, which is a real decision and not an
oversight, so here is the measurement: 0.33 ms of Python over a thousand
names, 4.2 ms over ten thousand, 32.5 ms over a hundred thousand. Every call
site runs it on a worker thread, inside a write transaction the handler was
already holding, and the read it adds is one TEXT column of a table the
sidebar already reads in full - with a correlated COUNT(*) per row - every
time it draws. So the marginal cost sits well under what the same data
already costs on an ordinary screen refresh.

UNIQUENESS IS A COURTESY AT CREATION TIME, NOT AN INVARIANT

Measured, and it matters for how much weight this file can carry: NO name
column in the schema has a UNIQUE constraint - not characters.name, not
chats.title, not personas.display_name, not boundaries.label. Two rows can
hold the same name today and nothing in the application stops them, because
a person is allowed to call two things the same thing. What this module does
is decline to CREATE a collision it can see. It does not promise there is
none, it cannot be relied on as one, and a caller that needs a real
uniqueness guarantee needs a database constraint instead.
"""

from __future__ import annotations

import re
from typing import Iterable

#: The prefix per surface. The interface is English throughout - there is no
#: i18n layer and not one Turkish string in the UI - so a generated name is
#: English too, and matches the wording of the button that creates the thing
#: ("New Chat" in the sidebar). This is the single source: tests IMPORT these
#: names rather than retyping them, so a reworded prefix cannot leave a test
#: asserting a string the application no longer produces.
CHAT = "New Chat"
CHARACTER = "New Character"
PERSONA = "New Persona"
PROXY = "Proxy"
VOICE = "New Voice"
BOUNDARY = "New Boundary"


def is_blank(value: str | None) -> bool:
    """Is this name empty? One definition, used by every call site.

    None, "", "   " and "\\t\\n" are all blank. Four different answers to this
    question used to be live at once: chats trimmed, characters raised, refs
    stored "   " verbatim and listed it as a voice with no visible name, and
    the pronunciation table dropped the row.
    """
    return not (value or "").strip()


def next_default_name(prefix: str, existing: Iterable[str | None]) -> str:
    """Build `prefix` + the smallest free number, given the names in use.

    "New Chat" over ["New Chat 1", "New Chat 3"] -> "New Chat 2".

    The rules, each on purpose:

    * SMALLEST FREE NUMBER, not max+1. Stateless, so no counter is stored and
      none can drift from the rows it counts; after 1 through 46 are deleted,
      max+1 would answer 48 for an empty list.
    * The scan IGNORES CASE. A chat the user named "new chat 2" makes
      "New Chat 2" a second row that looks identical on screen. The columns
      are plain TEXT, so SQLite compares them BINARY and would happily hold
      both - which is why the scan happens here in Python rather than in a
      WHERE clause.
    * casefold(), not lower(). lower() leaves a fold half done: "ß".lower()
      is "ß" while "ß".casefold() is "ss", so one German name typed two ways
      would be two rows that read alike. Measured, and so is the limit,
      stated rather than glossed: casefold does NOT fold the Turkish dotless
      "ı" onto "I". That fold is locale-dependent and a stored name carries
      no locale. (`re.IGNORECASE` does match that pair - also measured - and
      it is not used here for exactly that reason: it applies whatever fold
      the regex engine happens to implement, while an explicit casefold is
      one operation a reader can name and a test can break.) The prefixes
      are ASCII today; the rule is attached to the code so it is still right
      on the day one of them is not.
    * The name PRODUCED always carries the canonical spelling, whatever
      spelling the scan matched.
    * A hand-typed "New Chat 5" JOINS the scan and holds 5. Otherwise the
      generated name would eventually collide with a name a person chose.
    """
    pat = re.compile(rf"^{re.escape(prefix.casefold())}\s+(\d+)$")
    taken: set[int] = set()
    for name in existing:
        m = pat.match((name or "").strip().casefold())
        if m:
            taken.add(int(m.group(1)))
    n = 1
    while n in taken:
        n += 1
    return f"{prefix} {n}"


def name_or_default(value: str | None, prefix: str,
                    existing: Iterable[str | None]) -> str:
    """The one line the call sites actually use.

        title = name_or_default(body.title, default_names.CHAT, titles)

    Returns the trimmed name, or the next default when there is nothing to
    trim. After this call there is no such thing as a stored blank name, so
    the question "does this surface accept an empty one" stops having a
    per-surface answer.
    """
    if is_blank(value):
        return next_default_name(prefix, existing)
    return (value or "").strip()
