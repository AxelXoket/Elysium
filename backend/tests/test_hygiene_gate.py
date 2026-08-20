"""Behaviour tests for the source hygiene gate.

The gate exists to block a commit, so the thing worth testing is what it does
to content, not what its source says. Every test here hands `scan()` synthetic
files as (path, text) pairs. Nothing touches the repository, git, or the real
allowlist, which means these still pass on a machine where the tree happens to
be dirty and they still fail if the gate stops working on a tree that is clean.

Two things this file is careful about, both of them the trap the gate was built
after. `settings-copy.test.ts` once forbade the em dash and carried a literal em
dash inside its own regex; `verify_hygiene.py` would have flagged its own source
for the same reason. So a forbidden character is never typed here, only built:
EM_DASH is built with chr(), and the machine path is assembled from two pieces.
The gate scans this file like any other and must stay silent on it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# Loaded by path rather than imported, because backend/verify is not a package
# and putting it on sys.path used to make `verify_elysium_full` importable too -
# and importing THAT runs the entire regression suite as a side effect.
_GATE_PATH = Path(__file__).resolve().parent.parent / "verify" / "verify_hygiene.py"
_spec = importlib.util.spec_from_file_location("verify_hygiene", _GATE_PATH)
assert _spec and _spec.loader
hygiene = importlib.util.module_from_spec(_spec)
# Registered BEFORE exec_module, not after. @dataclass resolves annotations by
# looking its own class's __module__ up in sys.modules, so a module that runs
# before it is registered gets None there and dies on the first decorated
# class. The name has to match the one given to spec_from_file_location.
sys.modules["verify_hygiene"] = hygiene
_spec.loader.exec_module(hygiene)

#: Never typed. See the module docstring.
#: The idle-unload identifier is assembled the same way, so that this
#: file does not need an H-06 waiver to test H-06.
IDLE_SETTING = "TTS_IDLE_" + "UNLOAD_S"
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
#: Split so that this file does not itself contain a contiguous user path. The
#: first attempt split after the drive letter, which defeated the Windows form
#: and left the POSIX one whole - H-04 matches both, and the gate caught it.
USER_PATH = "C:/" + "Users/someone/secret.png"


def parse(text: str):
    return hygiene._parse_allowlist(text, "test-allowlist")


def scan_one(path: str, text: str, allowlist: str = ""):
    waivers, errors = parse(allowlist)
    hits = hygiene.scan([(path, text)], waivers)
    return hits, waivers, errors


class TestRulesFire:
    def test_an_em_dash_anywhere_in_the_tree_is_a_hit(self):
        hits, _, _ = scan_one("README.md", f"a sentence {EM_DASH} broken\n")
        assert [h.rule.rid for h in hits] == ["H-01"]

    def test_an_en_dash_is_a_hit_too(self):
        # The rule nothing else in the repo covered. settings-copy.test.ts
        # checks the em dash only, and the backend sweep in
        # test_speech_prep_cutters.py checks the em dash only.
        hits, _, _ = scan_one("frontend/src/x.tsx", f"pages 3{EN_DASH}5\n")
        assert [h.rule.rid for h in hits] == ["H-02"]

    def test_a_machine_path_is_a_hit(self):
        hits, _, _ = scan_one("backend/x.py", f'AVATAR = "{USER_PATH}"\n')
        assert [h.rule.rid for h in hits] == ["H-04"]

    @pytest.mark.parametrize("path", [
        "/" + "home/john/build.log",
        "/" + "Users/john/build.log",
        # The defect itself: an account name with a space in it. The Windows
        # form of the identical path was caught; this one was not, because the
        # POSIX branches demanded [A-Za-z0-9._-] and a space is none of those.
        "/" + "Users/John Smith/project/build.log",
        "/" + "home/Jose Ramirez/notes.txt",
        # Non-ASCII account names were the other half of the same charset.
        "/" + "home/" + chr(0x00FC) + "ser/x.txt",
        # And the trailing form with no file after it, which is how a path
        # usually appears in a comment.
        "/" + "Users/john/",
    ])
    def test_a_posix_user_path_is_a_hit_whatever_the_account_is_called(
        self, path: str
    ):
        hits, _, _ = scan_one("docs/x.md", f"see {path}\n")
        assert [h.rule.rid for h in hits] == ["H-04"], path

    @pytest.mark.parametrize("text", [
        # A path inside somebody's website is not a path on somebody's disk.
        "https:" + "//example.com/home/index.html",
        "fetch('" + "https://cdn.example.org/Users/avatar.png')",
    ])
    def test_a_url_that_merely_contains_the_word_is_not_a_hit(self, text: str):
        hits, _, _ = scan_one("frontend/src/x.ts", text + "\n")
        assert hits == [], text

    def test_a_system_path_with_no_account_name_in_it_is_not(self):
        # C:\Windows and /usr/lib carry no identity and appear legitimately in
        # comments about where Windows keeps things. A rule that fired on every
        # absolute path would be noise, and noise is how a gate gets bypassed.
        hits, _, _ = scan_one("backend/x.py", 'ROOT = "C:/Windows/Fonts"\n')
        assert hits == []

    def test_the_hit_carries_the_line_number_and_the_line_itself(self):
        # Both exist for the human reading the block. The line number is for
        # navigation only and is deliberately not part of any identity.
        text = f"first\nsecond\n  third {EM_DASH} here\n"
        hits, _, _ = scan_one("docs/x.md", text)
        assert hits[0].lineno == 3
        assert hits[0].text == f"third {EM_DASH} here"

    @pytest.mark.parametrize("path, caught", [
        ("frontend/src/a.tsx", True),
        ("frontend/src/lib/deep/a.ts", True),
        ("backend/a.py", False),           # wrong language entirely
        ("frontend/other/a.tsx", False),   # frontend, but not src
        ("frontend/src/a.js", False),      # src, but not TypeScript
        ("frontend/src/a.css", False),
    ])
    def test_a_rule_only_looks_where_its_scope_says(self, path, caught):
        # The boundaries, not just one inside and one outside. With only
        # "frontend/src/a.tsx" and "backend/a.py" as cases, _frontend_source
        # could widen to all of frontend/ or drop its extension filter and
        # every test would still pass.
        entity = "&" + "apos;"
        hits, _, _ = scan_one(path, entity)
        assert bool(hits) is caught

    def test_two_rules_firing_at_once_are_reported_separately(self, capsys):
        # Every other report test fires exactly one rule, so nothing would
        # notice one rule's example line printed under another rule's heading.
        text = f"a {EM_DASH} b\nc {EN_DASH} d\n"
        hits, waivers, errors = scan_one("docs/x.md", text)
        hygiene.report(hits, waivers, errors, {"docs/x.md"}, full=True)
        out = capsys.readouterr().out
        assert out.index("H-01") < out.index(f"a {EM_DASH} b")
        assert out.index(f"a {EM_DASH} b") < out.index("H-02")
        assert out.index("H-02") < out.index(f"c {EN_DASH} d")


    def test_the_allowlist_itself_is_never_scanned(self):
        # It quotes the exact text of every line it waives, so scanning it
        # would need a waiver for each quotation, and then a waiver for that
        # waiver's quotation. Excluded by scope, not by an entry inside itself:
        # a file that waives itself cannot be reviewed.
        hits, _, _ = scan_one(hygiene.ALLOWLIST_REL,
                              f"line: he wrote {EM_DASH} and meant it\n")
        assert hits == []

    def test_the_exclusion_is_only_that_one_file(self):
        neighbour = hygiene.ALLOWLIST_REL.replace("hygiene_allowlist",
                                                  "something_else")
        hits, _, _ = scan_one(neighbour, f"a {EM_DASH} b\n")
        assert [h.rule.rid for h in hits] == ["H-01"]


class TestTheGapsAnAdversarialPassFound:
    """Each of these was a confirmed miss before the rule was widened.

    They are kept as separate named cases rather than folded into a parametrize
    because the reason each one slipped is different, and a bare list of
    strings would lose that.
    """

    @pytest.mark.parametrize("path", [
        "D:" + "/Users/bob/build.log",          # any drive, not just C
        "E:" + "\\Users\\bob\\build.log",       # backslashes
        "\\\\" + "workstation\\Users\\bob\\x",  # UNC: a machine AND a person
    ])
    def test_a_user_path_is_caught_whatever_shape_it_arrives_in(self, path):
        hits, _, _ = scan_one("backend/x.py", f'P = "{path}"\n')
        assert [h.rule.rid for h in hits] == ["H-04"]

    @pytest.mark.parametrize("entity", ["&mdash;", "&#8212;", "&#X27;",
                                        "&nbsp;", "&apos;"])
    def test_any_html_entity_is_caught_not_a_hand_listed_few(self, entity):
        # The two dash entities in the list above are em dashes. With the old
        # hand-written list,
        # "fixing" an H-01 hit by HTML-encoding the character defeated every
        # rule at once: the entity rule did not know those names, and the dash
        # rules no longer saw a dash.
        hits, _, _ = scan_one("frontend/src/a.tsx", f"<p>{entity}</p>\n")
        assert [h.rule.rid for h in hits] == ["H-03"]

    @pytest.mark.parametrize("code", [0x2012, 0x2015, 0x2212, 0xFF0D])
    def test_a_dash_that_only_reads_like_one_is_still_caught(self, code):
        # Figure dash, horizontal bar, minus sign, fullwidth hyphen-minus.
        # Autocorrect and several IMEs produce these. A reader cannot tell
        # them from the two banned codepoints, so a tree full of them keeps
        # the check and not the rule.
        hits, _, _ = scan_one("docs/x.md", f"a {chr(code)} b\n")
        assert [h.rule.rid for h in hits] == ["H-05"]

    def test_the_ascii_hyphen_is_never_a_hit(self):
        # It is the replacement every one of these rules tells you to use.
        hits, _, _ = scan_one("docs/x.md", "a - b, and re-entry\n")
        assert hits == []


class TestNonUtf8SourceIsNamedNotSwallowed:
    """The quietest hole the adversarial pass found.

    A file saved as Windows ANSI holds an em dash as the single byte 0x97,
    which is not valid UTF-8. Decoding with errors="replace" turned it into
    U+FFFD before any pattern ran, so H-01 could not fire and the report called
    the file clean. This machine's console codepage is cp1254, so a Notepad
    "Save as ANSI" is not a hypothetical here.
    """

    ANSI_EM_DASH = b"a sentence \x97 broken\n"

    def test_the_bytes_really_do_hide_from_the_rule(self):
        # The premise, asserted rather than assumed. If this ever stops being
        # true the tests below are testing nothing.
        replaced = self.ANSI_EM_DASH.decode("utf-8", "replace")
        assert EM_DASH not in replaced

    def test_an_undecodable_file_is_reported(self):
        problems: list[str] = []
        hygiene.decode(self.ANSI_EM_DASH, "notes.md", problems)
        assert len(problems) == 1
        assert "notes.md" in problems[0]
        assert "UTF-8" in problems[0]

    def test_the_text_is_still_returned_so_other_rules_still_run(self):
        # Half a file is still worth scanning; the point is that the failure is
        # named, not that the file is skipped.
        text = hygiene.decode(self.ANSI_EM_DASH, "notes.md", [])
        assert "a sentence" in text and "broken" in text

    def test_clean_utf8_reports_nothing(self):
        problems: list[str] = []
        text = hygiene.decode(f"a {EM_DASH} b\n".encode("utf-8"), "x.md",
                              problems)
        assert problems == []
        assert EM_DASH in text


class TestWaivers:
    ALLOW = f"""
[H-01] docs/x.md
reason: quoted from an external document that is reproduced verbatim
line: he wrote {EM_DASH} and meant it
"""

    def test_a_waiver_suppresses_the_exact_line_it_names(self):
        hits, waivers, _ = scan_one(
            "docs/x.md", f"he wrote {EM_DASH} and meant it\n", self.ALLOW)
        assert hits == []
        assert waivers[0].used is True

    def test_editing_the_waived_line_makes_the_rule_fire_again(self):
        # THE property the whole design turns on. A waiver is pinned to text,
        # so a changed line is unreviewed text and has to be decided again. A
        # file-level or line-number waiver would have stayed silent here, which
        # is how an allowlist stops narrowing and starts covering the rule.
        hits, _, _ = scan_one(
            "docs/x.md", f"he typed {EM_DASH} and meant it\n", self.ALLOW)
        assert [h.rule.rid for h in hits] == ["H-01"]

    def test_a_waiver_does_not_cover_a_second_violation_in_the_same_file(self):
        text = (f"he wrote {EM_DASH} and meant it\n"
                f"and then wrote {EM_DASH} again\n")
        hits, _, _ = scan_one("docs/x.md", text, self.ALLOW)
        assert len(hits) == 1
        assert hits[0].lineno == 2

    def test_a_waiver_does_not_leak_into_another_file(self):
        hits, _, _ = scan_one(
            "docs/y.md", f"he wrote {EM_DASH} and meant it\n", self.ALLOW)
        assert len(hits) == 1

    def test_a_waiver_does_not_leak_into_another_rule(self):
        # Same file, same text, different character. H-02 was never waived.
        hits, _, _ = scan_one(
            "docs/x.md", f"he wrote {EN_DASH} and meant it\n", self.ALLOW)
        assert [h.rule.rid for h in hits] == ["H-02"]

    def test_indentation_is_not_part_of_the_anchor(self):
        # Reindenting a block is not a decision about its content, and a gate
        # that demanded a re-review after a reformat would be re-reviewed by
        # nobody.
        hits, _, _ = scan_one(
            "docs/x.md", f"        he wrote {EM_DASH} and meant it\n", self.ALLOW)
        assert hits == []

    def test_inserting_lines_above_does_not_disturb_the_waiver(self):
        # The failure that killed the file:line design. Here it is absent.
        text = f"new\nlines\nabove\nhe wrote {EM_DASH} and meant it\n"
        hits, waivers, _ = scan_one("docs/x.md", text, self.ALLOW)
        assert hits == []
        assert waivers[0].used is True

    def test_an_unused_waiver_is_visible_as_unused(self):
        # Reported as a failure by report(); the scan's job is to mark it.
        _, waivers, _ = scan_one("docs/x.md", "nothing here\n", self.ALLOW)
        assert waivers[0].used is False


class TestAllowlistParsing:
    def test_a_record_without_a_reason_is_rejected(self):
        waivers, errors = parse(
            f"[H-01] docs/x.md\nline: he wrote {EM_DASH} and meant it\n")
        assert waivers == []
        assert any("no reason" in e for e in errors)

    def test_a_record_without_a_line_anchor_is_rejected(self):
        # This is the shape that would waive a whole file forever.
        waivers, errors = parse("[H-01] docs/x.md\nreason: because\n")
        assert waivers == []
        assert any("anchor" in e for e in errors)

    def test_a_reason_may_wrap_across_indented_lines(self):
        waivers, errors = parse(
            "[H-01] docs/x.md\n"
            "reason: first part\n"
            "  and the rest of it\n"
            f"line: he wrote {EM_DASH} and meant it\n")
        assert errors == []
        assert waivers[0].reason == "first part and the rest of it"

    def test_a_backslash_path_is_read_as_the_same_file(self):
        # Windows shell completion produces backslashes and a human pasting one
        # into this file should not silently get a waiver that never matches.
        waivers, errors = parse(
            "[H-01] docs\\x.md\nreason: r\n"
            f"line: he wrote {EM_DASH} and meant it\n")
        assert errors == []
        assert waivers[0].path == "docs/x.md"

    def test_records_are_separated_by_blank_lines_not_by_guesswork(self):
        waivers, errors = parse(
            f"[H-01] a.md\nreason: r1\nline: x {EM_DASH} y\n"
            "\n"
            f"[H-02] b.md\nreason: r2\nline: p {EN_DASH} q\n")
        assert errors == []
        assert [(w.rid, w.path) for w in waivers] == [("H-01", "a.md"),
                                                      ("H-02", "b.md")]

    def test_comment_lines_are_not_records(self):
        waivers, errors = parse("# just a note\n# and another\n")
        assert (waivers, errors) == ([], [])


class TestTheReportIsJudgeable:
    """The owner's requirement: a block has to be decidable at a glance.

    Not decoration. A gate that prints only "blocked" sends the reader to go
    find out what happened, and the fastest way to find out is to run the
    commit again without the gate.
    """

    def _render(self, capsys, text="a {d} b\n", path="docs/x.md", allow=""):
        hits, waivers, errors = scan_one(path, text.format(d=EM_DASH), allow)
        hygiene.report(hits, waivers, errors, {path}, full=True)
        return capsys.readouterr().out

    def test_it_names_the_file_and_the_line_number(self, capsys):
        assert "docs/x.md:1" in self._render(capsys)

    def test_it_shows_the_offending_line(self, capsys):
        assert f"a {EM_DASH} b" in self._render(capsys)

    def test_it_explains_why_the_rule_exists(self, capsys):
        # Without this the reader can tell WHAT tripped and not whether it
        # should have, which is the only question they actually have.
        out = self._render(capsys)
        assert "HANDOFF" in out and "hyphen" in out

    def test_it_prints_a_waiver_record_that_can_be_pasted(self, capsys):
        # The escape hatch has to be easier to find than --no-verify, or it
        # will not be the one that gets used.
        out = self._render(capsys)
        assert "[H-01] docs/x.md" in out
        assert "reason:" in out
        assert f"line: a {EM_DASH} b" in out

    def test_a_stale_waiver_says_what_it_was_pinned_to(self, capsys):
        out = self._render(
            capsys, text="clean line\n",
            allow=f"[H-01] docs/x.md\nreason: r\nline: gone {EM_DASH} now\n")
        assert "match nothing" in out
        assert f"gone {EM_DASH} now" in out

    def test_a_stale_waiver_fails_the_run(self, capsys):
        hits, waivers, errors = scan_one(
            "docs/x.md", "clean line\n",
            f"[H-01] docs/x.md\nreason: r\nline: gone {EM_DASH} now\n")
        assert hygiene.report(hits, waivers, errors, {"docs/x.md"},
                              full=True) is False

    def test_a_clean_tree_passes(self, capsys):
        hits, waivers, errors = scan_one("docs/x.md", "clean line\n")
        assert hygiene.report(hits, waivers, errors, {"docs/x.md"},
                              full=True) is True


class TestAWaiverIsOnlyJudgedByARunThatReadItsFile:
    """The false alarm the hook produced on its first live commit.

    A commit touching one file scans one file. Every waiver belonging to some
    other file matched nothing, was called stale, and failed the run. Under
    that rule the only commit that could ever pass would be one that touched
    every waived file at once, which is to say none of them.
    """

    ELSEWHERE = ("[H-01] docs/other.md\nreason: r\n"
                 f"line: he wrote {EM_DASH} and meant it\n")

    def _waivers(self, text=ELSEWHERE):
        return parse(text)[0]

    def test_a_partial_run_says_nothing_about_a_file_it_did_not_read(self):
        waivers = self._waivers()
        assert hygiene.stale_waivers(waivers, {"docs/x.md"}, full=False) == []

    def test_a_partial_run_still_judges_a_file_it_did_read(self):
        # The waiver's own file was opened and nothing in it matched, so this
        # run has the evidence and the waiver is dead.
        waivers = self._waivers()
        dead = hygiene.stale_waivers(waivers, {"docs/other.md"}, full=False)
        assert [w.path for w in dead] == ["docs/other.md"]

    def test_a_full_sweep_judges_a_waiver_whose_file_is_gone(self):
        # Deleted or renamed. No commit will ever touch it again, so only the
        # sweep that reads everything can retire this entry - otherwise it sits
        # in the list forever, unjudged by construction.
        waivers = self._waivers()
        dead = hygiene.stale_waivers(waivers, {"docs/x.md"}, full=True)
        assert [w.path for w in dead] == ["docs/other.md"]

    def test_a_matched_waiver_is_never_stale_in_either_mode(self):
        waivers = self._waivers()
        waivers[0].used = True
        assert hygiene.stale_waivers(waivers, set(), full=True) == []
        assert hygiene.stale_waivers(waivers, set(), full=False) == []


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real git repository, with the gate pointed at it.

    Everything else in this file drives scan() on synthetic tuples, which is
    right for the rules and useless for the two functions the module docstring
    spends the most words justifying. worktree_files() and staged_files() are
    where the "two modes read different content" claim either holds or does
    not, and neither had a single test until an adversarial pass said so.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path,
                   check=True)
    monkeypatch.setattr(hygiene, "REPO_ROOT", str(tmp_path))
    return tmp_path


def write(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True)


class TestTheWorkingTreeMode:
    def test_it_reads_a_file_that_has_never_been_added(self, repo):
        # The blind spot that let this module's own first draft break H-01 on
        # the line under a comment forbidding it. A tracked-only listing looked
        # correct; the file most likely to break a rule is the new one.
        write(repo, "fresh.md", f"a {EM_DASH} b\n")
        assert dict(hygiene.worktree_files())["fresh.md"].strip() \
            == f"a {EM_DASH} b"

    def test_it_reads_the_disk_not_the_index(self, repo):
        # A developer who fixed something in their editor and has not staged it
        # must see the fixed content, or the sweep reports a violation they
        # already dealt with.
        write(repo, "a.md", f"a {EM_DASH} b\n")
        git(repo, "add", "a.md")
        write(repo, "a.md", "a - b\n")
        assert EM_DASH not in dict(hygiene.worktree_files())["a.md"]

    def test_it_honours_gitignore(self, repo):
        # Build output and the venv are not the tree's hygiene. Without this
        # the sweep would scan node_modules.
        write(repo, ".gitignore", "junk/\n")
        write(repo, "junk/generated.md", f"a {EM_DASH} b\n")
        assert "junk/generated.md" not in dict(hygiene.worktree_files())

    def test_it_skips_binary_content(self, repo):
        (repo / "blob.bin").write_bytes(b"\x00\x01\x02")
        assert "blob.bin" not in dict(hygiene.worktree_files())


class TestTheStagedMode:
    def test_it_reads_the_index_not_the_disk(self, repo):
        # The mirror of the test above, and the reason the two modes exist. At
        # the moment of commit, only what is staged matters; a half-finished
        # edit sitting in the working tree is nobody's business yet.
        write(repo, "a.md", f"a {EM_DASH} b\n")
        git(repo, "add", "a.md")
        write(repo, "a.md", "a - b\n")
        assert EM_DASH in dict(hygiene.staged_files())["a.md"]

    def test_it_ignores_a_file_that_is_only_on_disk(self, repo):
        write(repo, "unstaged.md", f"a {EM_DASH} b\n")
        assert dict(hygiene.staged_files()) == {}

    def test_a_staged_deletion_does_not_crash_it(self, repo):
        # A deleted path has no index entry left, so `git show :path` fails.
        # --diff-filter=ACMR is what keeps this from being an exception.
        write(repo, "a.md", "content\n")
        git(repo, "add", "a.md")
        git(repo, "commit", "-q", "-m", "add", "--no-verify")
        git(repo, "rm", "-q", "a.md")
        assert dict(hygiene.staged_files()) == {}

    def test_a_rename_presents_the_new_path(self, repo):
        write(repo, "old.md", "content\n")
        git(repo, "add", "old.md")
        git(repo, "commit", "-q", "-m", "add", "--no-verify")
        git(repo, "mv", "old.md", "new.md")
        staged = dict(hygiene.staged_files())
        assert "new.md" in staged and "old.md" not in staged


class TestTheCommandLine:
    """Four exits, one preamble.

    Every test here needs the same three things silenced: the allowlist, and
    the two hook checks, which answer about the real repository rather than the
    sandbox and would otherwise decide the exit code before the case under test
    gets a say. That preamble was written out four times. It is a fixture now.

    Deliberately NOT parametrised into one test. The four reach `main` through
    four different routes - a clean read, a rule hit, an argv flag, and a
    decode failure that no text fixture can even express - and folding them
    into a table would trade four sentences a reader understands for one table
    plus a discriminator, which is longer and says less.
    """

    @pytest.fixture(autouse=True)
    def _quiet_gates(self, repo, monkeypatch):
        monkeypatch.setattr(hygiene, "ALLOWLIST_PATH", str(repo / "none.txt"))
        monkeypatch.setattr(hygiene, "hook_mode_state", lambda *a: (True, "ok"))
        monkeypatch.setattr(hygiene, "hook_state", lambda *a: (True, "ok"))

    def test_a_clean_tree_exits_zero(self, repo):
        write(repo, "a.md", "a - b\n")
        assert hygiene.main([]) == 0

    def test_a_violation_exits_one(self, repo):
        write(repo, "a.md", f"a {EM_DASH} b\n")
        assert hygiene.main([]) == 1

    def test_the_staged_flag_selects_the_index(self, repo, capsys):
        # An argv typo here would silently turn every commit check into a
        # working-tree check, which is the wrong content at that moment.
        write(repo, "a.md", f"a {EM_DASH} b\n")
        assert hygiene.main(["--staged"]) == 0      # nothing staged, so clean
        assert "git index" in capsys.readouterr().out

    def test_an_undecodable_file_fails_the_run(self, repo):
        # write_bytes, not write. 0x97 is a cp1252 em dash and invalid UTF-8;
        # no text fixture can put it on disk, which is why this case cannot be
        # folded in with the others.
        (repo / "ansi.md").write_bytes(b"a sentence \x97 broken\n")
        assert hygiene.main([]) == 1


class TestTheHookIsFoundWhereGitLooksForIt:
    def test_the_default_is_the_hooks_directory(self, repo):
        assert hygiene.installed_hook_path().replace("\\", "/") \
            .endswith(".git/hooks/pre-commit")

    def test_core_hookspath_moves_it(self, repo):
        # Set routinely by dotfile setups and by Husky. A check hardcoded to
        # .git/hooks would diff a file git never consults and report a healthy
        # hook while the real one is missing.
        git(repo, "config", "core.hooksPath", "my-hooks")
        assert hygiene.installed_hook_path().replace("\\", "/") \
            .endswith("my-hooks/pre-commit")


class TestTheHookEntersHistoryExecutable:
    """The defect that is invisible on the machine that creates it.

    core.filemode is false in this repository, so git records a new file as
    100644 no matter how it was chmod'ed. Windows never notices, because it
    does not consult the bit. A POSIX clone gets a non-executable file in the
    hooks directory, and git does not run it and does not say so.
    """

    def _add(self, repo: Path, *flags: str) -> None:
        write(repo, "hook.sh", "#!/bin/sh\nexit 0\n")
        git(repo, "config", "core.filemode", "false")
        git(repo, "add", *flags, "hook.sh")

    def test_a_mode_644_entry_is_a_failure_that_says_the_fix(self, repo):
        self._add(repo)
        ok, message = hygiene.hook_mode_state("hook.sh")
        assert ok is False
        assert "100644" in message
        assert "--chmod=+x" in message

    def test_a_mode_755_entry_passes(self, repo):
        self._add(repo, "--chmod=+x")
        ok, _ = hygiene.hook_mode_state("hook.sh")
        assert ok is True

    def test_an_untracked_hook_is_a_failure(self, repo):
        # Not yet in git at all. Adding it without the flag is precisely how it
        # would enter history unrunnable, so this is the moment to say so.
        write(repo, "hook.sh", "#!/bin/sh\nexit 0\n")
        ok, message = hygiene.hook_mode_state("hook.sh")
        assert ok is False
        assert "not tracked" in message


class TestTheHookCannotDisappearQuietly:
    """.git/hooks is untracked, uncloned, and unrestored by any backup.

    A hook that lived only there would be absent on every fresh clone, and its
    absence looks identical to its presence: no diff, no failing test, no
    output. These tests are about the one thing that makes the absence visible.
    """

    def _pair(self, tmp_path: Path, source: bytes, installed: bytes | None):
        # write_bytes, deliberately. write_text on Windows opens with
        # newline=None and rewrites every "\n" as "\r\n", so the CRLF case
        # below would reach disk as "\r\r\n" and this suite would be testing
        # its own fixture rather than the comparison.
        src = tmp_path / "source"
        src.write_bytes(source)
        dst = tmp_path / "installed"
        if installed is not None:
            dst.write_bytes(installed)
        return hygiene.hook_state(str(src), str(dst))

    def test_a_matching_pair_is_fine(self, tmp_path):
        ok, _ = self._pair(tmp_path, b"#!/bin/sh\nrun\n", b"#!/bin/sh\nrun\n")
        assert ok is True

    def test_nothing_installed_is_a_failure_that_says_how_to_fix_it(self, tmp_path):
        ok, message = self._pair(tmp_path, b"#!/bin/sh\nrun\n", None)
        assert ok is False
        assert "no pre-commit hook is installed" in message
        assert "cp " in message

    def test_a_stale_installed_copy_is_a_failure(self, tmp_path):
        # The quiet one. Rules change in the versioned file, the developer
        # installed the hook months ago, and the commit is checked by an older
        # set of rules than the one the tree believes it has.
        ok, message = self._pair(tmp_path, b"#!/bin/sh\nnew\n",
                                 b"#!/bin/sh\nold\n")
        assert ok is False
        assert "differs" in message

    def test_line_endings_alone_are_not_a_difference(self, tmp_path):
        # core.autocrlf is true here and git for Windows runs hooks through a
        # shell that may rewrite them. A CRLF is not a change in behaviour, and
        # failing on one would train the reader to ignore this check.
        ok, _ = self._pair(tmp_path, b"#!/bin/sh\nrun\n",
                           b"#!/bin/sh\r\nrun\r\n")
        assert ok is True

    def test_the_install_command_is_shell_safe(self, tmp_path):
        # It is printed to be pasted. A Windows-native path would arrive at any
        # POSIX shell as one word with its separators eaten as escapes.
        _, message = self._pair(tmp_path, b"#!/bin/sh\nrun\n", None)
        command = [ln for ln in message.splitlines() if "cp " in ln][0]
        assert "\\" not in command

    def test_a_missing_versioned_source_is_also_a_failure(self, tmp_path):
        ok, message = hygiene.hook_state(str(tmp_path / "gone"),
                                         str(tmp_path / "also-gone"))
        assert ok is False
        assert "versioned hook is missing" in message


class TestBinaryContentIsSkipped:
    @pytest.mark.parametrize("name", ["Elysium.exe", "assets/icon.ico",
                                      "assets/logo.png"])
    def test_known_binary_suffixes(self, name):
        assert hygiene._is_binary(b"anything", name) is True

    def test_a_null_byte_decides_when_the_suffix_does_not(self):
        # The suffix list is a shortcut, not the contract. Elysium.exe is 29MB
        # and decoding it as UTF-8 to run four regexes over would be both an
        # exception and a waste.
        assert hygiene._is_binary(b"text\x00more", "mystery.dat") is True
        assert hygiene._is_binary(b"plain text", "mystery.dat") is False


class TestTheDeletionThatHasToStayDeleted:
    """H-06, and it is a different shape from every other rule here.

    The others forbid a character or a spelling. This one forbids the RETURN OF
    A FEATURE: a voice model is never taken off the card by a timer, it goes
    when the user swaps models or closes the app. The setting that unloaded it
    after N idle seconds was deleted rather than defaulted off, and a deletion
    leaves nothing in the tree to point at. Nothing in a diff says "and it must
    stay gone", so the idea comes back the next time somebody reads the VRAM
    budget and reaches for the obvious lever.
    """

    def test_the_setting_name_is_a_hit_wherever_it_appears(self):
        hits, _, _ = scan_one("backend/config.py", IDLE_SETTING + " = 600\n")
        assert [h.rule.rid for h in hits] == ["H-06"]

    def test_the_frontend_spelling_is_a_hit_too(self):
        """Scope is every text file, not backend Python. The setting had a
        control in the voice settings page, and half a deletion is worse than
        none: the dial would still be there, wired to nothing."""
        hits, _, _ = scan_one("frontend/src/settings.ts",
                              "export const " + IDLE_SETTING + " = 600\n")
        assert [h.rule.rid for h in hits] == ["H-06"]

    @pytest.mark.parametrize("line", [
        "phase: 'idle',",                        # the audio player's state
        "if vault_state.idle_seconds() > 900:",  # the auto lock, unrelated
        "const IDLE_CHUNK = 24;",                # ModelPanel's list rendering
        "# closes the idle connection after a minute",
    ])
    def test_the_word_idle_on_its_own_is_never_a_hit(self, line):
        """The rule has to be narrow or it gets waived into uselessness.

        There are dozens of legitimate uses of the word in this tree: the
        player's phases, the vault auto lock, the model list, half the
        connection comments. A rule that fired on those would be switched off
        within a week, and then the one thing it exists for would come back
        unnoticed.
        """
        hits, _, _ = scan_one("backend/anything.py", line + "\n")
        assert hits == []


class TestAnEncodedDashIsStillADash:
    """H-07, the gap an adversarial pass walked out through.

    H-03 catches every HTML entity, and only in frontend TypeScript. So the way
    to get an em dash past H-01 was to HTML-encode it in any other file: the
    character rule saw plain ASCII, the entity rule was out of scope, and the
    rendered document still showed an em dash to whoever read it. The gate was
    teaching its own workaround.
    """

    @pytest.mark.parametrize("entity", [
        "&" + "mdash;", "&" + "ndash;", "&#" + "8212;", "&#" + "8211;",
        "&#x" + "2014;", "&#X" + "2013;"])
    def test_a_dash_entity_outside_the_frontend_is_a_hit(self, entity):
        hits, _, _ = scan_one("docs/notes.md", "a sentence " + entity + " here\n")
        assert [h.rule.rid for h in hits] == ["H-07"]

    def test_it_does_not_fire_twice_on_a_frontend_file(self):
        """H-03 already covers those. Two hits on one line is noise, and noise
        is what makes a report harder to act on rather than more thorough."""
        hits, _, _ = scan_one("frontend/src/x.tsx",
                              "const t = 'a &" + "mdash; b';\n")
        assert [h.rule.rid for h in hits] == ["H-03"]

    def test_an_ordinary_entity_outside_the_frontend_is_left_alone(self):
        """Narrow on purpose. A document explaining that the ampersand entity
        escapes an ampersand is not a violation of anything, and a rule that
        says it is gets waived until it means nothing."""
        hits, _, _ = scan_one("docs/notes.md",
                              "write &" + "amp; for a literal\n")
        assert hits == []


class TestAFileTheGateCannotReadIsNeverSilent:
    def test_a_utf16_file_is_reported_rather_than_skipped(self, repo):
        """Found by an adversarial pass, and it is the cp1252 hole in a hat.

        Every ASCII character in UTF-16 carries a null byte, so `_is_binary`
        called the whole file binary and `worktree_files` dropped it with no
        entry in the report and none in `problems`. A forbidden character in a
        UTF-16 document was invisible AND silent, which is strictly worse than
        the cp1252 case: that one at least fails loudly.
        """
        (repo / "notes.md").write_bytes(
            ("a sentence " + EM_DASH + " broken\n").encode("utf-16"))
        subprocess.run(["git", "add", "notes.md"], cwd=repo, check=True)

        problems: list[str] = []
        files = dict(hygiene.worktree_files(problems))

        assert "notes.md" not in files, (
            "a UTF-16 file cannot be scanned and must not pretend to have been")
        assert any("notes.md" in p and "UTF-16" in p for p in problems), (
            "the file was skipped in silence, which is the whole defect: "
            f"problems={problems}")

    def test_a_utf16_file_with_no_byte_order_mark_is_reported_too(self, repo):
        """The half the first fix missed, and the half its docstring claimed.

        `utf-16-le` writes no BOM. The check looked at nothing else, so this
        file came back False, `_is_binary` still called it binary on its null
        bytes, and it went past in exactly the silence the check exists to
        end. Measured on 2026-08-17 before the fix: `problems` was empty.
        """
        (repo / "notes.md").write_bytes(
            ("a sentence " + EM_DASH + " broken, and long enough to have a "
             "shape\n").encode("utf-16-le"))
        subprocess.run(["git", "add", "notes.md"], cwd=repo, check=True)

        problems: list[str] = []
        files = dict(hygiene.worktree_files(problems))

        assert "notes.md" not in files
        assert any("notes.md" in p and "UTF-16" in p for p in problems), (
            f"the BOM-less form is still silent: problems={problems}")

    def test_the_big_endian_form_with_no_mark_is_caught_as_well(self, repo):
        # The null sits on the other side of every character. A check that
        # only knew one endianness would be half a check.
        (repo / "notes.md").write_bytes(
            ("a sentence " + EM_DASH + " broken, and long enough to have a "
             "shape\n").encode("utf-16-be"))
        subprocess.run(["git", "add", "notes.md"], cwd=repo, check=True)

        problems: list[str] = []
        list(hygiene.worktree_files(problems))
        assert any("UTF-16" in p for p in problems), problems

    def test_a_file_too_short_to_have_a_shape_is_not_guessed_at(self, repo):
        # The floor under the heuristic. Below it there is not enough evidence
        # to tell a document from a coincidence, and a check that guesses
        # anyway is how binaries start getting reported as documents.
        (repo / "tiny.dat").write_bytes(b"\x01\x00\x02\x00")
        subprocess.run(["git", "add", "tiny.dat"], cwd=repo, check=True)

        problems: list[str] = []
        list(hygiene.worktree_files(problems))
        assert problems == []

    def test_a_binary_that_scatters_nulls_is_not_mistaken_for_a_document(
        self, repo
    ):
        """The discriminating half of the shape test.

        UTF-16 keeps its nulls on one side of every pair. This file puts them
        on both, which is what real binary data does, and it must not be
        reported. Without this the check would complain about every untracked
        binary format and the complaints would stop being read.
        """
        (repo / "blob.dat").write_bytes(bytes(range(64)) * 4)
        subprocess.run(["git", "add", "blob.dat"], cwd=repo, check=True)

        problems: list[str] = []
        list(hygiene.worktree_files(problems))
        assert problems == []

    def test_a_real_binary_is_skipped_without_a_complaint(self, repo):
        """The other half of the same decision.

        Elysium.exe is 29 MB and tracked. A gate that complained about it on
        every run would train everyone to scroll past the complaints, and then
        the UTF-16 one above would go past too.
        """
        (repo / "logo.png").write_bytes(bytes([0x89]) + b"PNG" + bytes(64))
        subprocess.run(["git", "add", "logo.png"], cwd=repo, check=True)

        problems: list[str] = []
        list(hygiene.worktree_files(problems))
        assert problems == []
