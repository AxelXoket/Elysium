"""verify_hygiene.py - the source hygiene gate.

A set of house rules about the TEXT of this tree lived as scattered assertions
inside behaviour tests: settings-copy.test.ts forbade one character, a privacy
grep forbade another, and the rule against absolute machine paths existed only
in a person's memory until requirements.lock.txt shipped one 47 times. Rules
enforced in that shape are found at the wrong moment, by the wrong test, or not
at all.

This gate collects them. It is deliberately boring: read text, match a pattern,
print enough for a human to judge.

TWO MODES, AND WHY THEY READ DIFFERENT CONTENT
----------------------------------------------
    --staged    reads the git INDEX, only the files staged for commit.
    (default)   reads the WORKING TREE: every tracked text file, plus every
                new one git is not ignoring.

The first draft used the index for both, and that is wrong for the second. Run
from verify_elysium_full.py in the middle of a working session, an index read
reports on content the developer may have already fixed in their editor, and a
brand new file that has never been `git add`ed is invisible to it entirely. The
index is the right thing to check at the moment of commit and the wrong thing
to check at any other moment, so the caller picks.

It also happens to be the cheaper answer. Reading every tracked file through
one `git show` subprocess each was measured at roughly 16ms per spawn, which is
several seconds of pure process overhead before a single regex runs. The
working tree mode opens files directly: measured at about 0.2s for 502 files
against a 0.04s bare interpreter, so roughly 150ms of real work, not free but
not felt. The staged mode spawns per file and only ever sees the commit.

WHAT THE HOOK DOES NOT COVER, WHICH IS NOT A SMALL LIST
-------------------------------------------------------
`pre-commit` is a porcelain `git commit` concept, not a property of the
repository. Measured, not assumed: a `git merge` that produces a real merge
commit never runs it, and neither does `git rebase` for any commit it replays.
`git commit --amend` does run it. `core.hooksPath` can point git at an entirely
different hooks directory, which is why installed_hook_path() asks git instead
of assuming .git/hooks.

None of that is fixable from inside a pre-commit hook, and pretending otherwise
would be worse than saying it here. It is the reason the full sweep exists and
the reason it reads the working tree: whatever arrives through a merge or a
rebase is caught by the next sweep rather than at the moment it lands.

THE ALLOWLIST, AND WHY IT IS ANCHORED TO TEXT
---------------------------------------------
Some hits are correct. backend/tests/test_privacy_promises.py contains an
absolute user path because it is the test PROVING the app scrubs absolute user
paths; a rule that blocks it kills what it exists to protect. So there is an
escape hatch, and the shape of that hatch decides whether this gate still means
anything in a year.

It is NOT `git commit --no-verify`. That leaves no trace, and it switches off
every rule at once to get past one of them.

It is NOT a file-level exemption either. "waive H-04 in this file" never
narrows, only widens: once written it silently approves every future violation
anyone adds to that file, including ones nobody looked at. A waiver that cannot
expire is a hole with a comment next to it.

An entry therefore pins the EXACT TEXT of the line it waives. Edit that line
and the waiver stops matching, the rule fires again, and a human re-decides
against the new text. That is not a bug in the mechanism, it is the mechanism.
A waiver that matches nothing is itself a failure here, which is the only force
that ever makes the list shrink.

Line numbers appear in the report and never in the key. An anchor on
`file:line` moves the moment anything is inserted above it, and it then either
blocks a clean commit or, worse, silently waives whatever slid into that slot.
This is the same split `privacy_check` in verify_elysium_full.py already draws
between the lineno it prints and the substring it matches on.

KNOWN HOLES, WRITTEN DOWN RATHER THAN HALF FIXED
------------------------------------------------
A file staged with `git add -N` has an index entry with no real blob, so the
staged mode reads it as empty and passes it. Committing such a file commits
nothing of its content either, so nothing unreviewed reaches history through
this path, but the gate is silent rather than correct about it.

The three below came out of an adversarial pass on 2026-08-10 that was told to
get a forbidden character past this gate. Two of the five it found are fixed:
UTF-16 files are now complained about instead of dropped, and the dash
entities are caught outside frontend source by H-07. These three are not.

*An escape sequence produces no matching byte.* `"\\u2014"`, `chr(0x2014)` and
`"\\N{EM DASH}"` all put an em dash in front of a user and none of them put one
in a file. This is not a hole to close, it is the sanctioned way to write about
a forbidden character, and this file relies on it heavily. Anything that
reached a user through that route would have to be caught by a test of the
rendered copy, which is where it belongs.

*The binary check trusts the file name.* A plain text document named `.png` is
skipped without being read. Closing it means reading every byte of a 33 MB exe
on every run to learn what is already known from its name. The attack requires
deliberately misnaming a text file, which is not an accident anybody has.

*A text-anchored waiver covers every identical copy of that line.* If the same
line appears three times in one file, one record excuses all three, and only
one of them was ever looked at. The alternative is a line number in the key,
which was rejected for reasons that have not changed and are set out above. The
narrower widening is the better trade, and it is recorded here rather than
discovered later.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Iterable

# Named BACKEND_DIR, and resolved to the backend tree, because
# tests/test_verify_gate.py parametrizes over every verify_*.py and pins this
# expression. REPO_ROOT is what this script actually walks: the rules are about
# the whole published tree, frontend included, not about backend source.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BACKEND_DIR)

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWLIST_PATH = os.path.join(HERE, "hygiene_allowlist.txt")
#: The allowlist is never scanned. It has to quote the text of every line it
#: waives, so scanning it would mean each waiver needed a second waiver for its
#: own quotation, and that one a third. Excluded by scope rather than by an
#: entry inside itself, because a file that waives itself is not reviewable.
ALLOWLIST_REL = os.path.relpath(ALLOWLIST_PATH, REPO_ROOT).replace("\\", "/")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"

# Extensions whose bytes are not text. Elysium.exe is 33MB and tracked, and
# decoding it as UTF-8 to run a regex over would be both an exception and a
# waste. The null byte check in _is_binary catches anything this list misses.
BINARY_SUFFIXES = (".exe", ".png", ".ico", ".jpg", ".jpeg", ".gif", ".webp",
                   ".woff", ".woff2", ".ttf", ".zip", ".db", ".bin", ".pdf")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    rid: str
    what: str                       #: one line, what is forbidden
    why: str                        #: printed on every hit, so a block is judgeable
    pattern: re.Pattern
    scope: Callable[[str], bool]    #: (repo-relative path) -> does this rule apply
    blocking: bool = True


def _any_text(path: str) -> bool:
    return True


def _frontend_source(path: str) -> bool:
    return path.startswith("frontend/src/") and path.endswith((".ts", ".tsx"))


# Built with chr(), never typed. The earlier home of this rule,
# settings-copy.test.ts, put the literal em dash inside its own em dash regex
# and therefore broke the rule it enforced. The first draft of THIS file did
# the same thing two lines under a comment saying not to, and got away with it
# only because it was still untracked and the scan could not see it. That near
# miss is why worktree_files() now reads untracked files too.
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
#: Figure dash, horizontal bar, minus sign, small em dash, small hyphen-minus,
#: fullwidth hyphen-minus. Everything that reads as one of the two above
#: without being either of them. Deliberately excludes the ASCII hyphen, which
#: is the replacement this codebase is supposed to use.
DASH_LOOKALIKES = "".join(chr(c) for c in
                          (0x2012, 0x2015, 0x2212, 0xFE58, 0xFE63, 0xFF0D))

_DASH_WHY = (
    "Binding house rule (audit/HANDOFF.md section 1): no em dash and no en "
    "dash anywhere in source or documents. A plain hyphen, a comma, or a "
    "restructured sentence always works, and a rule with one exception in it "
    "stops being a rule."
)

RULES: tuple[Rule, ...] = (
    Rule(
        rid="H-01",
        what=f"em dash (U+2014, {EM_DASH})",
        why=_DASH_WHY,
        pattern=re.compile(EM_DASH),
        scope=_any_text,
    ),
    Rule(
        rid="H-02",
        what=f"en dash (U+2013, {EN_DASH})",
        why=_DASH_WHY,
        pattern=re.compile(EN_DASH),
        scope=_any_text,
    ),
    Rule(
        rid="H-03",
        what="HTML entity in TypeScript source (&apos; &quot; &amp; &#39;)",
        why=(
            "JSX does not need HTML entities in its text; they are the residue "
            "of escaping something that was never markup. They survive into "
            "places that render no HTML at all - a toast body, an aria-label, "
            "a window title - and the user reads the entity."
        ),
        # Any named or numeric entity, not a hand-listed few. The first version
        # listed apos/quot/amp and two numeric forms, and an adversarial pass
        # found that the named and numeric EM DASH entities slipped through
        # every rule at once: they are em dashes, so "fixing" an H-01 hit by
        # HTML-encoding it turned a caught violation into an uncaught one. The
        # uppercase-X numeric form was missed too. H-07 now carries the same
        # check into every file this one does not cover.
        pattern=re.compile(r"&[A-Za-z][A-Za-z0-9]*;|&#[Xx]?[0-9A-Fa-f]+;"),
        scope=_frontend_source,
    ),
    Rule(
        rid="H-04",
        what="absolute path carrying a machine account name",
        why=(
            "It publishes whose machine built this. requirements.lock.txt "
            "carried one 47 times into a public repository, because uv copies "
            "the constraint path it is handed into a provenance comment under "
            "every package. Use a relative path, or a path built at runtime."
        ),
        # A user directory specifically, not any absolute path: C:\Windows or
        # /usr/lib carry no identity and appear legitimately in comments.
        #
        # Any drive letter, not just C. And the UNC form, which an adversarial
        # pass found was invisible: a double-backslash share name followed by
        # Users and an account is a completely ordinary way a Windows path
        # leaks a machine AND a person, and it matched neither the drive-letter
        # branch nor the POSIX ones. Described rather than written out, because
        # writing one here would trip this rule, and a waiver for the rule's
        # own definition is the last thing this file should need.
        pattern=re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+"
                           r"|\\\\[^\\/]+[\\/]+Users[\\/]+"
                           r"|/home/[A-Za-z0-9._-]+/"
                           r"|/Users/[A-Za-z0-9._-]+/"),
        scope=_any_text,
    ),
    Rule(
        rid="H-05",
        what="a dash character that reads as an em or en dash",
        why=(
            "H-01 and H-02 forbid exactly two codepoints, and the house rule "
            "they enforce is about how the text READS. Word autocorrect and "
            "several IMEs produce these instead: figure dash, horizontal bar, "
            "minus sign, the small and fullwidth forms. A reader cannot tell "
            "them apart from the two that are banned, so a tree that allows "
            "them is not keeping the rule, only passing the check."
        ),
        pattern=re.compile("[" + DASH_LOOKALIKES + "]"),
        scope=_any_text,
    ),
    Rule(
        rid="H-07",
        what="an HTML entity that renders as an em or en dash",
        why=(
            "The same violation as H-01 and H-02, spelled differently. A "
            "reader of the rendered document sees an em dash and cannot know "
            "it was written as six ASCII characters. H-03 already catches this "
            "in frontend source; everywhere else it was invisible, so the way "
            "to get an em dash past this gate was to HTML-encode it - which is "
            "to say, the gate taught the workaround."
        ),
        # Only the dash entities, and only outside the files H-03 already
        # covers. A general entity rule over the whole tree would fire on every
        # legitimate `&amp;` in a document about escaping, and a rule that
        # cries wolf gets waived into uselessness. Found by an adversarial pass
        # on 2026-08-10 that walked out through this exact gap.
        pattern=re.compile(r"&(?:mdash|ndash);|&#(?:8212|8211);"
                           r"|&#[Xx](?:2014|2013);", re.IGNORECASE),
        scope=lambda path: not _frontend_source(path),
    ),
    Rule(
        rid="H-06",
        what="the idle-unload setting, which was deleted on purpose",
        why=(
            "A voice model is never taken off the card by a timer. It goes "
            "when the user swaps models or closes the app, and at no other "
            "moment. The setting that unloaded it after N idle seconds was "
            "removed rather than defaulted off, because a load costs tens of "
            "seconds and a user who steps away for a coffee should not pay it "
            "again. This rule exists because a deletion has no shape: nothing "
            "in a diff says 'and it must stay gone', so the idea comes back "
            "the next time somebody reads the VRAM budget and reaches for the "
            "obvious lever."
        ),
        # The identifier, not the word. `idle` alone appears legitimately all
        # over this tree: the player's idle phase, the vault's idle_seconds
        # auto-lock, ModelPanel's IDLE_CHUNK, half the connection comments. A
        # rule that fired on all of those would be turned off within a week.
        #
        # Assembled from halves so the banned identifier never appears whole on
        # any line of this file. Same reasoning as EM_DASH = chr(0x2014) above:
        # a rule whose own definition needs a waiver has already lost, and the
        # allowlist is not the place to record that a rule exists.
        pattern=re.compile("TTS_IDLE_" + "UNLOAD" + "|IDLE_" + "UNLOAD_S"),
        scope=_any_text,
    ),
)


# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------

@dataclass
class Waiver:
    rid: str
    path: str
    reason: str
    line: str
    source_lineno: int              #: where in the allowlist file, for the report
    used: bool = False


def _parse_allowlist(text: str, origin: str) -> tuple[list[Waiver], list[str]]:
    """Three-line records, blank-line separated. Returns (waivers, errors).

    Chosen over JSON because the whole point of this file is to be read in a
    diff by a human deciding whether a waiver is still honest. A record looks
    like what it waives:

        [H-01] docs/quotes.md
        reason: reproduced verbatim from a document this app does not own, so
          the punctuation is not ours to change
        line: he wrote it that way, and the page shows it that way

    Everything after "line: " is taken literally to end of line, so no quoting
    rules apply to the waived text itself. A reason may continue onto indented
    continuation lines.

    The example above is deliberately not an H-04 one. The first draft used a
    realistic machine path here and the gate flagged its own docstring, which
    is the correct behaviour and a bad example.
    """
    waivers: list[Waiver] = []
    errors: list[str] = []
    header = re.compile(r"^\[([A-Z]+-\d+)\]\s+(\S+)\s*$")

    rid = path = reason = line = None
    start = 0

    def flush() -> None:
        nonlocal rid, path, reason, line
        if rid is None:
            return
        if not reason or not reason.strip():
            errors.append(f"{origin}:{start}: [{rid}] {path} has no reason. "
                          f"A waiver without a written reason is a hole.")
        elif line is None:
            errors.append(f"{origin}:{start}: [{rid}] {path} has no 'line:' "
                          f"anchor, so it would waive the whole file forever.")
        else:
            waivers.append(Waiver(rid, path, reason.strip(), line.strip(), start))
        rid = path = reason = line = None

    for n, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("#"):
            continue
        if not raw.strip():
            flush()
            continue
        match = header.match(raw)
        if match:
            flush()
            start = n
            rid, path = match.group(1), match.group(2).replace("\\", "/")
            reason, line = None, None
            continue
        if rid is None:
            errors.append(f"{origin}:{n}: text outside any record: {raw.strip()!r}")
        elif raw.startswith("reason:"):
            reason = raw[len("reason:"):]
        elif raw.startswith("line:"):
            line = raw[len("line:"):]
        elif reason is not None and line is None and raw[:1].isspace():
            reason += " " + raw.strip()          # wrapped reason
        else:
            errors.append(f"{origin}:{n}: expected 'reason:' or 'line:', got "
                          f"{raw.strip()!r}")
    flush()
    return waivers, errors


def load_allowlist() -> tuple[list[Waiver], list[str]]:
    if not os.path.exists(ALLOWLIST_PATH):
        return [], []
    with open(ALLOWLIST_PATH, encoding="utf-8") as handle:
        text = handle.read()
    return _parse_allowlist(text, os.path.relpath(ALLOWLIST_PATH, REPO_ROOT))


# ---------------------------------------------------------------------------
# Content sources
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                          check=True).stdout.decode("utf-8", "replace")


def _is_binary(data: bytes, path: str) -> bool:
    return path.lower().endswith(BINARY_SUFFIXES) or b"\x00" in data[:8000]


def _looks_like_utf16(data: bytes) -> bool:
    """A file this gate would otherwise drop on the floor without a word.

    Found by an adversarial pass on 2026-08-10, and it is the cp1252 hole
    wearing a different hat. Every ASCII character in UTF-16 carries a null
    byte, so `_is_binary` calls the whole file binary, `worktree_files` skips
    it, and it appears in no report and no problems list. An em dash in a
    UTF-16 document was invisible AND silent, which is strictly worse than the
    cp1252 case that gets a loud encoding failure.

    Decoding it here was the other option and was rejected: this repository has
    no UTF-16 file and should not grow one, so the useful answer is a complaint
    rather than quiet accommodation.
    """
    return data[:2] in (b"\xff\xfe", b"\xfe\xff")


def decode(data: bytes, rel: str, problems: list[str] | None = None) -> str:
    """UTF-8, and a loud complaint when it is not.

    The first version decoded with errors="replace" and that was a silent hole,
    not a convenience. A file saved as Windows ANSI holds an em dash as the
    single byte 0x97, which is not valid UTF-8; "replace" turns it into U+FFFD
    before any pattern runs, so H-01 cannot fire and the report says the file
    is clean. This machine's own console codepage is cp1254, so a Notepad
    "Save as ANSI" is not a hypothetical here.

    The text is still returned, replacements and all, because the other rules
    should still get their pass over the readable parts. The difference is that
    the file is now named as undecodable, and that alone fails the run.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if problems is not None:
            problems.append(
                f"{rel} is not valid UTF-8 (byte {exc.start} of "
                f"{len(data)}). Any rule about a character cannot see past "
                f"this, so the file is reported rather than quietly scanned.")
        return data.decode("utf-8", "replace")


def worktree_files(problems: list[str] | None = None) -> Iterable[tuple[str, str]]:
    """Every tracked or new text file, read from disk. Yields (relpath, text).

    --others --exclude-standard adds files that exist but are not tracked yet,
    while still honouring .gitignore so build output and the venv stay out. A
    tracked-only listing looked correct and was the worst possible blind spot:
    the file most likely to break a rule is the one being written right now,
    and it is invisible until the moment it is too late to catch cheaply. This
    module's own first draft broke H-01 on the line under a comment forbidding
    it, and the scan reported clean.
    """
    for rel in _git("ls-files", "-z", "--cached", "--others",
                    "--exclude-standard").split("\0"):
        if not rel:
            continue
        full = os.path.join(REPO_ROOT, rel)
        try:
            with open(full, "rb") as handle:
                data = handle.read()
        except OSError:
            # Tracked but absent from disk: a deletion the developer has not
            # staged yet. Nothing to scan, and not this gate's business.
            continue
        if _is_binary(data, rel):
            # Skipped, but not in silence when it is text this gate cannot
            # read. See _looks_like_utf16.
            if _looks_like_utf16(data) and problems is not None:
                problems.append(f"{rel}: UTF-16, which this gate cannot read "
                                f"and will not scan. Save it as UTF-8.")
            continue
        yield rel, decode(data, rel, problems)


def staged_files(problems: list[str] | None = None) -> Iterable[tuple[str, str]]:
    """Files staged for commit, read from the index. Yields (relpath, text).

    --diff-filter=ACMR is load bearing twice. It drops D, whose paths no longer
    have an index entry at all, and it drops U, where a mid-merge path has no
    stage 0 and `git show :path` fails outright rather than returning nothing.
    """
    names = _git("diff", "--cached", "--name-only", "-z",
                 "--diff-filter=ACMR").split("\0")
    for rel in names:
        if not rel:
            continue
        try:
            data = subprocess.run(["git", "show", f":{rel}"], cwd=REPO_ROOT,
                                  capture_output=True, check=True).stdout
        except subprocess.CalledProcessError:
            continue
        if _is_binary(data, rel):
            # Skipped, but not in silence when it is text this gate cannot
            # read. See _looks_like_utf16.
            if _looks_like_utf16(data) and problems is not None:
                problems.append(f"{rel}: UTF-16, which this gate cannot read "
                                f"and will not scan. Save it as UTF-8.")
            continue
        yield rel, decode(data, rel, problems)


# ---------------------------------------------------------------------------
# Scanning and reporting
# ---------------------------------------------------------------------------

#: The tracked file the installed hook is supposed to be a copy of. Compared as
#: bytes rather than a hash: the difference is what a human needs to see.
HOOK_SOURCE = os.path.join(HERE, "hooks", "pre-commit")
HOOK_SOURCE_REL = os.path.relpath(HOOK_SOURCE, REPO_ROOT).replace("\\", "/")


def installed_hook_path() -> str:
    """Where git will ACTUALLY look for the hook.

    Not a hardcoded .git/hooks. `core.hooksPath` redirects the whole directory
    elsewhere and is set routinely by dotfile setups and by tooling like Husky
    or the pre-commit framework. With it set, a check that diffs .git/hooks is
    comparing a file git never consults, and it reports a healthy hook while
    the real one is missing. Asking git removes the guess.
    """
    try:
        out = _git("rev-parse", "--git-path", "hooks/pre-commit").strip()
    except Exception:                                      # pragma: no cover
        return os.path.join(REPO_ROOT, ".git", "hooks", "pre-commit")
    return out if os.path.isabs(out) else os.path.join(REPO_ROOT, out)


def hook_mode_state(rel_source: str = HOOK_SOURCE_REL) -> tuple[bool, str]:
    """Is the versioned hook recorded in git as EXECUTABLE?

    The defect this exists for is invisible on the machine that creates it.
    `core.filemode` is false in this repository, so git ignores the on-disk
    executable bit and records a new file as mode 100644 no matter how it was
    chmod'ed. Windows never notices, because Windows does not consult the bit.

    A POSIX clone does. git restores mode 644, `cp` does not invent an
    executable bit that is not there, and a non-executable file in the hooks
    directory is not run: no error, no warning, no output at all. The content
    comparison passes, the report says the hook is installed and current, and
    not one commit is ever checked.

    Read from the index rather than from disk, because the index is what will
    be committed and the disk is not.
    """
    fix = f"git add --chmod=+x {rel_source}"
    try:
        entry = _git("ls-files", "-s", "--", rel_source).strip()
    except Exception:                                      # pragma: no cover
        return True, "could not read the index; mode unchecked"
    if not entry:
        return False, (f"{rel_source} is not tracked yet. Stage it with the "
                       f"executable bit or it enters history unrunnable:\n"
                       f"        {fix}")
    mode = entry.split()[0]
    if mode != "100755":
        return False, (f"{rel_source} is recorded as mode {mode}, not 100755. "
                       f"A POSIX clone will check it out non-executable and "
                       f"git will silently never run it:\n        {fix}")
    return True, "versioned hook is executable in the index"


def hook_state(source_path: str = HOOK_SOURCE,
               installed_path: str | None = None) -> tuple[bool, str]:
    """Is the versioned hook the one git will run? Returns (ok, message).

    This check exists because the failure it looks for is otherwise completely
    silent. .git/hooks is not tracked and not cloned, so on a fresh clone the
    hook is simply absent, and an absent hook produces no diff, no failing
    test, and no output. Two enforcement paths were the point of this design;
    without this check one of them resets to off every time the repository is
    copied, and nothing says so.
    """
    if installed_path is None:
        installed_path = installed_hook_path()

    def read(path: str) -> bytes | None:
        try:
            with open(path, "rb") as handle:
                # Normalized because git for Windows writes hooks through a
                # shell that may rewrite line endings, and a CRLF difference is
                # not a difference in what the hook does.
                return handle.read().replace(b"\r\n", b"\n")
        except OSError:
            return None

    want = read(source_path)
    if want is None:
        return False, (f"the versioned hook is missing from "
                       f"{os.path.relpath(source_path, REPO_ROOT)}")
    have = read(installed_path)
    # Forward slashes, always. This line is meant to be copied into a shell,
    # and every shell that runs a git hook treats a backslash as an escape:
    # the Windows-native form of this path would arrive as one mangled word.
    rel_source = os.path.relpath(source_path, REPO_ROOT).replace("\\", "/")
    # The destination is whatever git said, not a hardcoded .git/hooks.
    rel_dest = os.path.relpath(installed_path, REPO_ROOT).replace("\\", "/")
    install = f"cp {rel_source} {rel_dest}"
    if have is None:
        return False, (f"no pre-commit hook is installed, so nothing checks a "
                       f"commit at the moment it is made.\n"
                       f"        install it with:  {install}")
    if have != want:
        return False, (f"the installed pre-commit hook differs from the "
                       f"versioned one.\n"
                       f"        replace it with:  {install}")
    return True, "pre-commit hook installed and current"


@dataclass
class Hit:
    rule: Rule
    path: str
    lineno: int
    text: str


def scan(files: Iterable[tuple[str, str]], waivers: list[Waiver]) -> list[Hit]:
    by_key: dict[tuple[str, str], list[Waiver]] = {}
    for waiver in waivers:
        by_key.setdefault((waiver.rid, waiver.path), []).append(waiver)

    hits: list[Hit] = []
    for rel, text in files:
        if rel == ALLOWLIST_REL:
            continue
        applicable = [r for r in RULES if r.scope(rel)]
        if not applicable:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            for rule in applicable:
                if not rule.pattern.search(line):
                    continue
                waived = False
                for waiver in by_key.get((rule.rid, rel), ()):
                    if waiver.line == stripped:
                        waiver.used = True
                        waived = True
                if not waived:
                    hits.append(Hit(rule, rel, lineno, stripped))
    return hits


def _clip(text: str, width: int = 96) -> str:
    return text if len(text) <= width else text[:width - 3] + "..."


def stale_waivers(waivers: list[Waiver], scanned_paths: set[str],
                  full: bool) -> list[Waiver]:
    """Waivers this run is entitled to call dead.

    "Unused" and "stale" are not the same thing, and conflating them made the
    hook reject every commit on its first live run. A commit touching one file
    scans one file, so the waivers belonging to the other two files matched
    nothing - through no fault of their own - and were reported as dead. Under
    that rule the only commit that could ever pass would be one that happened
    to touch every waived file at once.

    So a waiver is judged only when this run actually read its file. Anything
    else is a question this run did not ask.

    The full sweep reads every tracked and untracked file, so it may also judge
    a waiver whose file is not there at all: that one points at something
    deleted or renamed, and it will otherwise sit in the list forever, unjudged
    by every commit because no commit ever touches a file that does not exist.
    """
    dead = []
    for waiver in waivers:
        if waiver.used:
            continue
        if waiver.path in scanned_paths or full:
            dead.append(waiver)
    return dead


def report(hits: list[Hit], waivers: list[Waiver], errors: list[str],
           scanned_paths: set[str], full: bool) -> bool:
    """Print everything a human needs to decide. Returns True when clean."""
    ok = True

    for message in errors:
        print(f"  [{FAIL}] allowlist  {message}")
        ok = False

    blocking = [h for h in hits if h.rule.blocking]
    warning = [h for h in hits if not h.rule.blocking]

    for group, tag in ((blocking, FAIL), (warning, WARN)):
        by_rule: dict[str, list[Hit]] = {}
        for hit in group:
            by_rule.setdefault(hit.rule.rid, []).append(hit)
        for rid in sorted(by_rule):
            found = by_rule[rid]
            rule = found[0].rule
            print(f"\n  [{tag}] {rid}  {rule.what}  ({len(found)} found)")
            print(f"        why: {rule.why}")
            for hit in found:
                print(f"    {hit.path}:{hit.lineno}")
                print(f"      {_clip(hit.text)}")
            example = found[0]
            print(f"\n        If one of these is correct and must stay, add it "
                  f"to\n        {os.path.relpath(ALLOWLIST_PATH, REPO_ROOT)} "
                  f"as:\n")
            print(f"          [{rid}] {example.path}")
            print(f"          reason: <why this specific line is right>")
            print(f"          line: {example.text}")
    if blocking:
        ok = False

    stale = stale_waivers(waivers, scanned_paths, full)
    if stale:
        print(f"\n  [{FAIL}] allowlist  {len(stale)} waiver(s) match nothing")
        print("        why: a waiver is pinned to the exact text of the line "
              "it excuses.\n"
              "        When that line is edited or removed the waiver stops "
              "matching, and it\n"
              "        has to be re-decided against the new text rather than "
              "carried forward.\n"
              "        This is the only thing that ever makes this list "
              "shrink, so it is a\n"
              "        failure and not a warning. Delete the entry, or "
              "re-anchor it.")
        for waiver in stale:
            origin = os.path.relpath(ALLOWLIST_PATH, REPO_ROOT)
            print(f"    {origin}:{waiver.source_lineno}  "
                  f"[{waiver.rid}] {waiver.path}")
            print(f"      was pinned to: {_clip(waiver.line)}")
        ok = False

    used = sum(1 for w in waivers if w.used)
    # "considered" rather than "in use": in staged mode most waivers belong to
    # files this run never opened, and reporting them as unused would say a
    # commit is carrying dead weight when it is only carrying a small diff.
    considered = sum(1 for w in waivers if w.used or w.path in scanned_paths)
    print(f"\n  scanned {len(scanned_paths)} file(s), {len(RULES)} rule(s), "
          f"{used}/{considered} waiver(s) matched of those considered "
          f"({len(waivers)} in the file)")
    return ok


def main(argv: list[str]) -> int:
    staged = "--staged" in argv
    waivers, errors = load_allowlist()

    problems: list[str] = []
    files = list(staged_files(problems) if staged else worktree_files(problems))
    source = "git index (staged for commit)" if staged else "working tree"

    print("=" * 62)
    print("  Source hygiene gate")
    print("=" * 62)
    print(f"  reading: {source}")

    hits = scan(files, waivers)
    ok = report(hits, waivers, errors, {rel for rel, _ in files}, full=not staged)

    for problem in problems:
        print(f"\n  [{FAIL}] encoding  {problem}")
        ok = False

    # The mode check runs in BOTH modes, unlike the installed-copy check. It is
    # about what the commit is going to publish, and the commit that publishes
    # a 644 hook is exactly the one this should stop.
    mode_ok, mode_message = hook_mode_state()
    print(f"\n  [{PASS if mode_ok else FAIL}] hook mode  {mode_message}")
    ok = ok and mode_ok

    # Only in working tree mode. In staged mode this script IS what the hook
    # invoked, so asking whether a hook is installed answers itself, and a
    # failure here would block the commit over a condition the commit has
    # already disproved.
    if not staged:
        hook_ok, message = hook_state()
        print(f"\n  [{PASS if hook_ok else FAIL}] hook  {message}")
        ok = ok and hook_ok

    print()
    if ok:
        print(f"  [{PASS}] hygiene clean")
    else:
        print(f"  [{FAIL}] hygiene violations - see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
