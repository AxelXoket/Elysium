"""Nothing in this repository says who told the author what to do.

Every commit here goes out under one name, to a public remote. Prose that
names a second party - "the owner's decision", "on the owner's instruction",
"the owner said" - leaves a permanent, uncorrectable record of a
conversation rather than a record of the software. A hundred and twenty
seven such phrases were found and removed on 2 September 2026, across
seventy nine files; without a gate the count starts climbing again the next
time somebody writes down why a default is what it is.

SO THE RULE IS MECHANICAL, not remembered. Say WHAT was decided and WHY.
Do not say WHO decided it.

    "Owner's decision, 8 August 2026."   ->  "Decided 8 August 2026."
    "the owner's rule is that a note     ->  "a note never disappears"
     never disappears"
    "the strict half the owner asked     ->  "the strict half"
     for"

DELIBERATELY UNDER-INCLUSIVE, and that is the honest trade. It matches the
shapes that actually occurred rather than trying to reason about English:
a possessive followed by a decision noun, `owner` followed by a decision
verb, and a handful of fixed phrases. Somebody determined to write an
attribution can still slip one past it. What it stops is the ordinary case,
which is the case that produced all hundred and twenty seven.

THE WORD ITSELF IS NOT BANNED. `owner` is a precise technical term in three
places this codebase cares about - a Windows ACL owner and the OWNER RIGHTS
SID, the process that owns a lock, and the `boundaries.owner` column - and
those uses are correct. The pattern below matches a PERSON who ruled,
instructed or asked, and leaves the rest alone. That is why it is a phrase
list rather than a word list.

WIDENED 5 September 2026, after a sweep of 638 files found sixteen lines
this file scored 0 out of 16 on. Not because it was broken: the ground
control still fired on every shape it was written for. It had simply swept
its own catalogue clean in September and everything left over was a shape
it had never looked at. A gate that passes because the prose moved on is a
gate that reads as protection nobody has to check.

Six shapes were added, and one design change that matters more than any of
them:

    agent                  a second writer, named        3 lines
    assistant-actor        a tool, named                 1 line
    tool-name              codex, copilot, chatgpt       2 lines
    owner-arbiter          "owner's ask", "owner
                           decision", "an owner who
                           decides"                      5 lines
    user-design-call       a DESIGN decision pinned on
                           a person                      4 lines
    reviewer-second-party  "a reviewer read it the
                           other way"                    1 line

THE DESIGN CHANGE IS THE WRAP. Four of those sixteen were split across two
physical lines by ordinary comment reflow:

    * ... is an open question for the
    *  owner rather than something this comment settles

No line-by-line pattern can ever see that, and widening the word list does
not help: measured, the old pattern scores 0 out of 16 in wrap mode too. So
`_hits` now tries the line, and failing that the line joined to the NEXT one
with comment markers stripped. The reported line is always the FIRST of the
pair. Measured on the whole tree: joining adds zero new hits, so the wrap
buys the four and costs nothing.

`assistant` CANNOT BE GATED AS A BARE WORD, and pretending otherwise would
be the thing that gets this file switched off. The product's own data model
owns that word: `role="assistant"`, the `is-assistant` CSS class, the
messages CHECK constraint, "assistant row", "assistant reply". 522 lines use
it correctly. A bare `\\bassistant\\b` matched 1567 lines on the first try.
So `assistant-actor` is a short list of ACTOR collocations only, it caught
exactly one real line (`.gitignore`, "Assistant / editor tooling state"),
and an actor sentence phrased some other way will still walk straight past
it. That is a known hole, written down rather than papered over.

NOISE IS ASSERTED, NOT HOPED FOR. `test_no_single_pattern_is_noisy` runs
each pattern over the tree ON ITS OWN and names any one that lights up. The
next person to widen this file finds out which pattern drowned the tree
instead of discovering that the whole gate got switched off.

SCOPE IS TRACKED FILES, and that sentence now means something it did not
mean before. `docs/` was skipped by name until 5 September 2026, which was
the right answer for nine of the eleven documents in it and the wrong answer
for the other two: `docs/frontend_contract.md` is the contract of record
between the two halves of the app and `docs/AUDIT_2026_07_CLOSURE.md` is a
closure record, and BOTH go to the remote. They were being skipped for the
wrong reason, and a directory-shaped skip has no way to tell them apart from
the internal planning beside them.

So the skip was dropped and the question handed to `git ls-files`, which
already answers it exactly: the other nine are `docs/*` in `.gitignore` with
two negations, they are untracked, they never enter the repository, and the
rule is about text that ENTERS THE REPOSITORY. A file that never leaves this
machine is not in scope no matter what it says.

STILL NOT SCANNED: `frontend/src/test/lib/draftStore.test.ts` carries a real
NUL byte that makes every tree-wide text gate treat it as binary. Recorded
elsewhere; not this file's to fix.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: A person who decided, instructed or owns, one named shape per entry.
#:
#: Named rather than welded into one blob so that `test_no_single_pattern_is
#: _noisy` can run them separately. A pattern that starts matching half the
#: repository is then a named failure instead of a mystery.
#:
#: Deliberately phrase-shaped throughout: a bare `owner`, `agent` or
#: `assistant` is usually the technical sense, and banning the words would
#: make this gate a nuisance somebody switches off.
_PATTERNS: dict[str, str] = {

    # The original three shapes, unchanged. Every one of them still fires;
    # widening this file must never narrow it.
    "owner-decision":
        # "the owner's rule", "Owner's decision", "the owner's own vault"
        r"\bowner'?s\s+(ruling|decision|call|instruction|answer|rule"
        r"|choice|requirement|own|real|personal|machine|desktop|brief|word"
        r"|karar)\b"
        # "the owner said", "the owner chose", "what the owner typed"
        r"|\bowner\s+(said|asked|chose|added|wants|takes|kept|needs|typed"
        r"|set|suspended|should|made|reads|described|reported|removed"
        r"|confirmed|complained|named|runs|saw|explicitly)\b"
        # the fixed phrases, in both languages
        r"|\bper\s+the\s+owner\b|\bfrom\s+the\s+owner\s+directly\b"
        r"|\bon\s+(the\s+)?owner'?s?\s+instruction"
        r"|\bsahibin\s+karar|\bsahip\s+karar"
        r"|\bwe\s+were\s+told\b|\bas\s+instructed\b",

    # A SECOND WRITER, named. The determiner is required on purpose:
    # `a backup agent, an indexer or an antivirus` (crypto.py) is a class of
    # Windows process and has to stay legal, so only the shapes that read as
    # a colleague are matched.
    "agent":
        r"\b(another|an|the|independent|separate|second|reviewing|other)"
        r"\s+(sub-?)?agent\b",

    # A TOOL, named. See the docstring: this cannot be a word list, because
    # `assistant` is the product's own word for the other half of a chat.
    "assistant-actor":
        r"\bassistant\s*/\s*(editor|ide|tool)"
        r"|\b(coding|ai)\s+assistant\b"
        r"|\bassistant\s+(wrote|drafted this|was asked|decided"
        r"|added this|tooling)\b",

    # Other tools by name. Provider names that are part of a MODEL ID
    # (`openai/gpt-4o`, `anthropic/claude-3.5-sonnet`) are deliberately
    # absent: those are product data on an OpenRouter client, not a
    # confession about who typed the file.
    "tool-name":
        r"\b(codex|copilot|chatgpt|cursor\s+(ui|ide)|windsurf|aider"
        r"|devin)\b",

    # The owner as the person who SETTLES a question. Distinct from
    # `owner-decision` above, which needs a possessive or a speech verb;
    # these are the apostrophe-less and relative-clause forms that walked
    # past it ("an open owner decision", "an owner who decides").
    "owner-arbiter":
        r"\bowner'?s\s+(ask|asks|acceptance|criterion|preference|wish"
        r"|want|intent|vault|note|verdict)\b"
        r"|\bowner\s+(decision|decisions|call|ruling|saying|deciding"
        r"|asked|wanted)\b"
        r"|\b(for|to|by)\s+the\s+owner\s+(rather|to\s+(decide|settle"
        r"|call))\b"
        r"|\ban\s+open\s+owner\b|\ban\s+owner\s+who\s+decides\b",

    # A DESIGN decision pinned on a person, and this one has to stay narrow.
    # "the user chose"/"the user asked for" is usually RUNTIME product prose
    # and correct: the user really did pick that file, really did press
    # delete. `routers/vault.py` saying "throwing it away is the user's call
    # to make once they trust the vault" is a sentence about the app, not
    # about a conversation. What is matched instead is the shape that
    # explains a CONSTANT or a POLICY by pointing at somebody: a
    # parenthetical aside, a contrast against the code itself, and a request
    # quoted verbatim.
    "user-design-call":
        r"\(\s*the\s+user'?s\s+call\b"
        r"|\bthe\s+user'?s\s+call,\s+not\b"
        # The quote character is written as an escape rather than typed, so
        # this file stays pure ASCII: a curly quote sitting in a source file
        # is exactly the sort of thing the tree hygiene gate exists to argue
        # about, and there is no reason to give it the argument.
        r"|\bthe\s+user\s+asked\s+for\s+[\"']"
        r"|\bthe\s+user\s+asked\s+for\s+" "\\u201c"
        r"|\bthe\s+user\s+asked\s+for\s+(breathing\s+room|more\s+frosted"
        r"|a\s+proper|room\s+to)\b",

    # A reviewer who HELD A VIEW. Impersonal future-reader prose is left
    # alone on purpose: "a required sentence is a line a reviewer reads"
    # describes the artefact, not a second party in a conversation.
    "reviewer-second-party":
        r"\ba\s+reviewer\s+(read|argued|objected|disagreed|flagged|said"
        r"|pointed|noted|thought)\b"
        r"|\breviewers?\s+(said|asked|ruled|objected|disagreed)\b",
}

ATTRIBUTION = re.compile(
    "|".join(f"(?:{p})" for p in _PATTERNS.values()), re.I)

#: AI HOUSE STYLE, the fifteen words that appear ZERO times in the tree
#: today. Guarding a zero is nearly free; the cleanup once they are spread
#: through eighty files is not, which is the whole argument for adding this
#: before it is needed rather than after.
#:
#: FOUR WORDS FROM THE FULL HOUSE LIST ARE MISSING HERE, each because it has
#: a real technical sense in this repository and banning it would cost more
#: than it saves:
#:
#:   underscore   the name of the `_` character (5 lines)
#:   landscape    a photo orientation and a window shape (3 lines)
#:   significant  inside a QUOTE from encode/httpx#2423 (1 line)
#:   robust       the section banner "robustness" in a test (1 line)
#:
#: If one of those four ever shows up as house style rather than as a
#: technical term, it gets caught by a person, not by this list.
_AI_VOCABULARY = (
    "delve", "crucial", "comprehensive", "nuanced", "multifaceted",
    "furthermore", "moreover", "pivotal", "tapestry", "foster", "showcase",
    "intricate", "vibrant", "fundamental", "interplay",
)

AI_VOCABULARY = re.compile(
    r"\b(" + "|".join(_AI_VOCABULARY) + r")(s|d|es|ed|ing|ly|ness|ity)?\b",
    re.I)

#: WHAT IS NOT SWEPT, and `docs/` is deliberately no longer on this list.
#:
#: It used to be, which quietly cost the sweep two files that DO go to the
#: remote: `docs/frontend_contract.md` and `docs/AUDIT_2026_07_CLOSURE.md`.
#: Everything else under `docs/` is internal planning that never leaves this
#: machine, and the rule is about text that ENTERS THE REPOSITORY, so those
#: are correctly out of scope - but they were out for the wrong reason, and
#: a directory-shaped skip cannot tell the two cases apart.
#:
#: `git ls-files` already can. It lists tracked files and nothing else, so
#: the nine ignored documents never reach this sweep no matter what is in
#: this tuple, and the two tracked ones now do. `.gitignore` says exactly
#: that in exactly one place (`docs/*` with two negations), and duplicating
#: it here as a hand-written allowlist would be a second copy to keep in
#: step. When a tenth document is added or one of the two is retired, git
#: is the only thing that has to know.
_SKIP_DIRS = ("frontend/node_modules/", "assets/")
_SKIP_EXT = (".png", ".exe", ".drawio", ".pdf", ".lock", ".ico", ".wav",
             ".jpg", ".jpeg", ".webp", ".svg", ".woff", ".woff2", ".ttf")

#: Below this the sweep is not reading the repository and a clean result
#: means nothing. Calibrated at 645 tracked files on 2 September 2026.
_FILE_FLOOR = 400


#: THIS FILE, and it is the only exemption there is.
#:
#: A gate that forbids a phrase has to write that phrase down: the pattern
#: quotes it, the ground control plants it, and the docstring shows what to
#: write instead. Scanning itself, it reports its own examples and refuses
#: every commit forever. Excluded by exact path so the hole is one file
#: wide and cannot be widened by accident, and named here rather than
#: buried in a skip list.
_THIS_FILE = "backend/tests/test_no_attribution_prose.py"


def _tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=_ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:                              # pragma: no cover
        pytest.skip("not a git checkout")
    return [_ROOT / f for f in out.stdout.split()
            if not f.startswith(_SKIP_DIRS) and not f.endswith(_SKIP_EXT)
            and f != _THIS_FILE]


#: Comment furniture, so that a sentence reads the same to this gate
#: whether it sits in prose, a `#` comment, a `*` docstring margin or a
#: `//` line. Only ever used for MATCHING; what gets reported is the
#: untouched source line.
_MARGIN = re.compile(r"^\s*(\*/|/\*+|\*|//|#:|#|--|<!--)\s?")


def _bare(line: str) -> str:
    return _MARGIN.sub("", line).strip()


def _hits(paths, pattern: re.Pattern[str] | None = None) -> list[str]:
    """Every line matching `pattern`, the line itself or wrapped onto the next.

    THE WRAP IS THE POINT. Comment reflow splits a sentence wherever the
    margin falls, and four of the sixteen lines this gate was widened for
    were split that way - "an open question for the / owner rather than".
    Matching physical lines can never see those, so a line that comes up
    clean is retried joined to the one after it.

    Anchored at the FIRST line of the pair, because that is where the
    sentence starts and where somebody has to go to rewrite it. Measured
    over the whole tree: joining adds no hits of its own, so the wrap costs
    nothing to keep.
    """
    rx = ATTRIBUTION if pattern is None else pattern
    found = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:                                  # pragma: no cover
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            joined = _bare(line)
            if not rx.search(line) and not rx.search(joined):
                if i >= len(lines):
                    continue
                joined = f"{joined} {_bare(lines[i])}"
                if not rx.search(joined):
                    continue
            try:
                rel = p.relative_to(_ROOT).as_posix()
            except ValueError:
                rel = p.name            # a scratch file from a control
            found.append(f"{rel}:{i}\n      {line.strip()[:100]}")
    return found


class TestNobodyIsQuotedInThisRepository:
    def test_no_file_names_a_person_who_decided(self) -> None:
        paths = _tracked()
        assert len(paths) >= _FILE_FLOOR, (
            f"the sweep found only {len(paths)} files, which is below the "
            f"floor of {_FILE_FLOOR}. A clean result from a sweep that read "
            f"almost nothing is not a clean result.")

        found = _hits(paths)

        assert found == [], (
            f"{len(found)} line(s) name a person who decided or instructed. "
            f"Say WHAT was decided and WHY, not WHO decided it:\n    "
            + "\n    ".join(found))

    def test_the_exemption_is_one_file_wide(self) -> None:
        """POSITIVE CONTROL for the hole this file cuts in itself.

        An exclusion is the classic way a gate stops meaning anything, so
        the size of it is asserted rather than trusted: exactly one path,
        and it has to be this one.
        """
        swept = {p.relative_to(_ROOT).as_posix() for p in _tracked()}
        assert _THIS_FILE not in swept, "the exemption is not in effect"
        assert Path(_ROOT / _THIS_FILE).exists(), (
            "the exempted path does not exist, so the exemption is a typo "
            "and something else is silently being scanned or skipped")
        # Every OTHER test file in the same directory is still swept.
        siblings = {p for p in swept
                    if p.startswith("backend/tests/test_")}
        assert len(siblings) > 100, (
            f"only {len(siblings)} test files reached the sweep; the "
            f"exclusion is wider than one file")

    def test_every_published_document_is_swept(self) -> None:
        """The scope change, asserted rather than remembered.

        `docs/` was skipped wholesale until 5 September 2026 and nobody
        noticed that some of the files under it ship. The failure mode is
        silent by construction - a skipped directory produces exactly the
        same clean result as a clean directory - so the fix comes with a
        test that can tell those two states apart.

        Both halves matter. Every tracked document must be IN, or the skip
        has grown back. The untracked ones must be OUT, or this sweep has
        started reading private planning notes and will fail on prose that
        was never going anywhere.

        Which documents those are is asked of git, not written down here.
        The set has already changed once: AUDIT_2026_07_CLOSURE.md was
        published and is not any more, and a hand-written pair would have
        turned that into a red test rather than a non-event.
        """
        swept = {p.relative_to(_ROOT).as_posix() for p in _tracked()}

        # Whatever else lives under docs/, the sweep sees exactly the
        # tracked files and nothing more. Asked of git rather than of a
        # hand-written list, so a tenth document needs no edit here.
        out = subprocess.run(["git", "ls-files", "docs/"], cwd=_ROOT,
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        tracked_docs = {f for f in out.stdout.split()
                        if not f.endswith(_SKIP_EXT)}
        # FLOOR. Two empty sets are equal, so the comparison below passes
        # with the sweep switched off entirely. At least one document under
        # docs/ ships; if that ever stops being true, delete this test
        # rather than letting it go quiet.
        assert tracked_docs, (
            "git reports no tracked prose under docs/, so the comparison "
            "below would pass without measuring anything")
        assert {p for p in swept if p.startswith("docs/")} == tracked_docs, (
            "the sweep's view of docs/ and git's disagree; an untracked "
            "planning note is being read, or a tracked one is not")

    def test_the_detector_fires(self, tmp_path) -> None:
        """GROUND CONTROL. An empty result and a broken regex look identical
        from the outside, and this file's whole value is the empty result."""
        sample = tmp_path / "sample.py"
        sample.write_text(
            "# Owner's decision, 8 August 2026: report was not enough.\n"
            "# The rule is that a note never disappears.\n",
            encoding="utf-8")
        found = _hits([sample])
        assert len(found) == 1, f"expected exactly the first line, got {found}"
        assert "Owner's decision" in found[0]

    @pytest.mark.parametrize("line", [
        "#: The OWNER RIGHTS well known SID. Without an ACE for it",
        '"the folder is still reachable by a group that is not the owner"',
        "owner = getattr(module, node.value.id, None)",
        "-- Scope and owner say the same thing, or the row is a lie",
        "# Passive: this is a viewer, not the owner of the setting.",
        "the lock owner is a process id, not a person",
        # Added 5 September 2026 with the six new shapes. Every line below
        # is real and had to survive the widening: these are the exact
        # senses the new patterns come closest to eating.
        "# a handle is exactly what a backup agent, an indexer or an",
        '        json={"selected_model_id": "anthropic/claude-3.5-sonnet"},',
        '    { ...modelFixture, id: "openai/gpt-4o" },',
        "# (OpenAI-style tiling, Anthropic w*h/750); 1100 is conservative",
        '  return ("[Notebook - unverified notes an assistant drafted "',
        '  <div className="message-bubble-shell is-assistant">',
        "    if (message.role === \"assistant\") return true;",
        "the user ever wrote, so throwing it away is the user's call to make",
        "# 404 IS the deletion the user asked for - the chat is already gone",
        "  * different from the one the user asked for - a model that never",
        '"""The model the USER chose. None means extraction does not run."""',
        "# A boolean flag would be one keystroke; a sentence is a line a",
        "  * than relying on a reviewer noticing.",
        "reviewer reads. Three records use it today, all three because",
    ])
    def test_the_technical_sense_is_not_touched(self, tmp_path, line) -> None:
        """POSITIVE CONTROL, and the reason this is a phrase list.

        A gate that banned the word would have deleted a Windows SID name, a
        schema column and an AST-walk variable. Every line here is real, from
        this repository, and every one of them must pass.
        """
        sample = tmp_path / "sample.py"
        sample.write_text(line + "\n", encoding="utf-8")
        assert _hits([sample]) == [], f"the technical sense was flagged: {line}"

    #: One planted line per new shape. Keyed by pattern name so a shape that
    #: silently stops matching is named, not just counted.
    _GROUND = {
        "owner-decision":
            "# Owner's decision, 8 August 2026: report was not enough.",
        "agent":
            "# rewritten by another agent while this is being written",
        "assistant-actor":
            "# Assistant / editor tooling state, machine-local.",
        "tool-name":
            " * Future Codex UI will display this in a dialog.",
        "owner-arbiter":
            "# whether to encrypt it at all is an open owner decision",
        "user-design-call":
            "FAILURE POLICY (the user's call, and the right one): stop.",
        "reviewer-second-party":
            "# A reviewer read it the other way and that reading is why",
    }

    @pytest.mark.parametrize("name", sorted(_PATTERNS))
    def test_every_shape_still_fires(self, tmp_path, name) -> None:
        """GROUND CONTROL, one per shape.

        The old three shapes are in here beside the six new ones on
        purpose. The failure this guards against is not a pattern that
        never worked, it is a pattern that WORKED and then got narrowed by
        somebody widening a neighbour: an alternation is easy to break from
        two lines away and nothing about the result looks broken.
        """
        sample = tmp_path / "sample.py"
        sample.write_text(self._GROUND[name] + "\n", encoding="utf-8")
        rx = re.compile(_PATTERNS[name], re.I)
        assert _hits([sample], rx), (
            f"the {name} pattern no longer fires on its own example: "
            f"{self._GROUND[name]!r}")
        # and the combined pattern must still contain it
        assert _hits([sample]), (
            f"{name} fires alone but not once merged into ATTRIBUTION, so "
            f"the alternation is dropping it")

    def test_the_wrap_sees_a_split_sentence(self, tmp_path) -> None:
        """GROUND CONTROL for the wrap, which is the half with no other net.

        Without this the wrap could quietly stop working and every result
        would still look exactly the same, because the four lines it was
        built for get rewritten in the same commit that adds it.
        """
        sample = tmp_path / "sample.tsx"
        sample.write_text(
            " * What follows is an open question for the\n"
            " *  owner rather than something this comment settles.\n",
            encoding="utf-8")
        found = _hits([sample])
        assert len(found) == 1, (
            f"the wrap did not join the pair, or joined it twice: {found}")
        assert ":1\n" in found[0], (
            f"the wrap must report the FIRST line of the pair: {found[0]}")

    def test_no_single_pattern_is_noisy(self) -> None:
        """The claim that this widening cost nothing, asserted rather than
        remembered.

        Each shape is run over the whole tree ON ITS OWN. A pattern that
        starts matching ordinary prose does not make this gate stricter, it
        makes it useless: the next person sees hundreds of failures, cannot
        tell signal from noise, and switches the file off. When that day
        comes this test names the guilty pattern instead of leaving somebody
        to bisect an alternation by hand.

        Zero is the right threshold and not an arbitrary one. Every shape
        here matches an attribution and nothing else, so any hit is either a
        real violation - which the sweep above reports properly - or noise.
        """
        paths = _tracked()
        assert len(paths) >= _FILE_FLOOR, "the sweep read almost nothing"

        noisy = {}
        for name, pat in _PATTERNS.items():
            hits = _hits(paths, re.compile(pat, re.I))
            if hits:
                noisy[name] = hits

        assert noisy == {}, "\n".join(
            f"the {name} pattern matched {len(hits)} line(s):\n    "
            + "\n    ".join(hits[:12])
            + ("\n    ..." if len(hits) > 12 else "")
            for name, hits in noisy.items())


class TestTheHouseStyleWordsStayAtZero:
    """The fifteen words are at zero today, and staying there is the cheap
    half of the job.

    There is no cleanup to do here and that is exactly why it goes in now.
    Every one of the sixteen attributions this gate was widened for was
    cheaper to prevent than it was to find: the sweep that found them read
    638 files, and the gate that should have caught them scored zero.
    """

    def test_no_file_uses_the_house_vocabulary(self) -> None:
        paths = _tracked()
        assert len(paths) >= _FILE_FLOOR, (
            f"the sweep found only {len(paths)} files, below the floor of "
            f"{_FILE_FLOOR}. A clean result from a sweep that read almost "
            f"nothing is not a clean result.")

        found = _hits(paths, AI_VOCABULARY)

        assert found == [], (
            f"{len(found)} line(s) use a word from the house list. Say the "
            f"plain thing instead:\n    " + "\n    ".join(found))

    @pytest.mark.parametrize("word", _AI_VOCABULARY)
    def test_the_detector_fires_on_every_word(self, tmp_path, word) -> None:
        """GROUND CONTROL, per word.

        Parametrised rather than one planted paragraph: a single typo in a
        fifteen-way alternation is invisible in a combined test that passes
        on the other fourteen.
        """
        sample = tmp_path / "sample.md"
        sample.write_text(f"This is a {word} matter.\n", encoding="utf-8")
        assert _hits([sample], AI_VOCABULARY), f"{word} is not detected"

    @pytest.mark.parametrize("line", [
        # The four words held back, in their real technical senses. If a
        # later hand adds one of them to the list, these go red first and
        # say why it was left out.
        "# The leading underscore on the last one is deliberate: it is the",
        "A phone writes the sensor's landscape frame and an Orientation tag",
        '`str.splitlines()` (encode/httpx#2423, "resulting in significant',
        "# -- robustness ------------------------------------------------",
    ])
    def test_the_four_technical_senses_are_left_alone(
            self, tmp_path, line) -> None:
        sample = tmp_path / "sample.py"
        sample.write_text(line + "\n", encoding="utf-8")
        assert _hits([sample], AI_VOCABULARY) == [], (
            f"a technical sense was flagged as house style: {line}")
