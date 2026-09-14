"""The model folder scan finds a model wherever a real user put it, and says
so when it could not look.

Seven findings, each measured against the real registry before the fix:

  1. the walk only ever identified CHILDREN of a root, so a root that IS the
     model folder was a silent miss;
  2. one directory budget was shared by every root, so a bloated first root
     left the second unscanned;
  3. `truncated` reached the wire and was dropped by the client (frontend
     half; the payload half is pinned here);
  4. a root that does not exist, is a file or sits on a removed drive was
     skipped in silence;
  5. extra_roots were used raw: `~`, `%VAR%` and relative paths were taken
     literally, the same root twice listed a model twice under one uid, and a
     root inside another root listed one folder under two uids;
  6. HuggingFace cache bookkeeping (`.no_exist/<rev>`, `trees`, `.locks`,
     `xet`) showed up as "not recognised" rows;
  7. depth 3 missed HF_HOME (hub/models--X/snapshots/<rev>) and the ModelScope
     cache, both of which sit at depth 4.

Five more from a cold review of that fix, each shown red before it was closed:

  8. a root inside an earlier root was DROPPED on the claim that the outer
     walk reached its folders anyway. It does not past the depth limit or
     past the directory cap: on a real hub cache with the limits lowered,
     [hub, hub/models--X/snapshots] found nothing where [snapshots] alone
     found the model, and nothing said so;
  9. the default drop folder was identified as a model when loose files sat
     in it, which hid every proper subfolder under it with no row and no flag;
 10. a junction reached one real folder from two places, and each place was a
     row; a junction back into its own root listed one model at every depth;
 11. `truncated` could not say WHICH root was cut, and the settings page told
     the user to point the scan at a smaller folder, a control that does not
     exist;
 12. a subfolder that refused to be listed mid-walk vanished without a log line.

Six more from a second cold review, each probed against the fix above with
real junctions before it was closed:

 13. a junction inside a root pointing OUTSIDE it fell back to the bare folder
     name for the uid, so two junctions to two different model folders of the
     same name were two rows under one uid (a duplicate React key, shared
     settings, the first row loaded whichever was picked);
 14. the unlistable-subfolder warning named the folder relative to its root,
     and a root above the profile folder (C:\\ or C:\\Users, the mis-pointed
     root the config comment itself names) put the account name in the log;
 15. a root that held files matching no engine (a Piper or Kokoro folder
     pointed at directly) was neither a model nor an unrecognised row;
 16. a root that is itself a model, inside a wider root, was re-identified
     under its own uid with no legacy uid, so a user who added the parent
     after pointing at the model lost the active selection;
 17. the step-around compared the child's lexical key against the inner
     root's canonical key, so a junction from inside the outer root into the
     inner root listed the model under the outer root's uid;
 18. the claim that a model folder named like HF bookkeeping is still found
     had no test that would go red if the name check moved above identify_dir.

Seven more from a third cold review, probed with real junctions against the
fix above before each was closed:

 19. the first walk to reach a real folder kept it, and the step-around only
     knew the roots INSIDE the current root, so a junction from one root into
     a sibling root (or from the drop folder into an extra root) listed the
     model under the wrong root, with a uid that depended on the root order
     and on a junction in an unrelated folder, and the uid it had under its
     own root was gone with no legacy uid: a saved selection answered 400;
 20. only the child that IS the junction was compared by where it goes, so a
     folder BELOW a junction into a plain folder was compared by its spelling
     alone and the inner root under it never got its row;
 21. a root holding any file was a row before its walk began, and that row
     absorbed every row under it: a hub with a version.txt listed itself
     instead of the snapshot the user has to look at;
 22. only tts_runtime._resolve followed a legacy uid; GET /tts/active and the
     settings routes matched the uid alone, so the chat drew the old uid as
     "error" with no readiness until a speak ran;
 23. the selection rewrite left the model's saved values under the old uid;
 24. the ModelScope layout in the config comment and in this file was made
     up (models/<org>--<name>/snapshots/master); the real cache is
     hub/models/<org>/<name> with the files flat;
 25. a junction back to the root's own parent lets every revisit of the root
     count against its directory cap, now said in the scan docstring.

Five more from a fourth cold review, probed with real junctions before each
was closed:

 26. the fix for 13 moved a junction child that leaves its root onto the
     spelling uid and gave it digest("", spelling) as its legacy, a uid no
     build ever produced; every shipped build, 1.1.0 to 1.1.6, gave that
     folder digest(root, bare name), so a user with models\\hf -> D:\\hf and
     hf\\xtts_v2 selected updated into tts_model_unknown with the tuned values
     orphaned. The shipped uid is the legacy; two junctions to two folders of
     one name share it and it resolves to the first row, the one 1.1.6 loaded;
 27. the root above a root was found by canonical key only, so a junction
     ROOT inside a wider root ([wide, wide\\jlink], jlink -> elsewhere\\model)
     carried no legacy uid although [wide] alone listed it at wide\\jlink;
 28. a root holding files was a "not recognised" row when everything under
     it belonged to an inner root, or to a root reached through a junction,
     where the root alone listed the folder below it and never itself;
 29. the contract listed six bookkeeping names against seven in the code;
 30. the engine override resolved the model folder before confining it, so
     every model behind a junction inside a root answered 400 to a manual
     engine pick while the scan listed it.

Every test builds its folders in tmp_path; none reads the machine's caches.
The synthetic model builders come from test_tts_core so the two files agree
on what a model looks like. Every uid expectation is computed with the recipe
written out in _recipe and named by the three helpers below it, never with
registry._uid_for, so a change to the recipe turns these red instead of
moving the expectation along with it. The helpers say which uid a test
means: _uid_yesterday is the uid a folder had in every shipped build,
_uid_of_root_model the uid a root that is itself a model has (introduced
with finding 1, no shipped build listed a root as a model), and
_uid_by_spelling the uid a junction child that leaves its root has today
(introduced with finding 13; its shipped uid is _uid_yesterday(root, name)).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path

import pytest

import config
from tests.test_tts_core import _touch, make_fish, make_xtts
from tts.registry import _HF_BOOKKEEPING, scan_roots
from tts.runtimes import extra_roots


def _write_registry(extra: list[str]) -> None:
    """The app-owned runtimes.json, with only the part the scan reads.

    conftest's autouse fixture already points TTS_RUNTIMES_PATH at a file of
    its own, so this never touches the developer's real registry."""
    reg = Path(config.TTS_RUNTIMES_PATH)
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"extra_roots": extra}), encoding="utf-8")


def _canon(p: Path | str) -> str:
    return os.path.realpath(str(p))


def _recipe(root: Path, rel: str) -> str:
    """The uid recipe, spelled out in full: the resolved root as a posix
    string, a NUL, rel, casefolded, sha256, first sixteen hex digits. The
    same recipe in every build since 1.1.0; what moved between builds is
    the rel a folder is given, which the three helpers below name."""
    raw = f"{root.resolve().as_posix()}\x00{rel}"
    return hashlib.sha256(raw.casefold().encode("utf-8")).hexdigest()[:16]


def _uid_yesterday(root: Path, rel: str) -> str:
    """The uid a CHILD folder had in every shipped build, 1.1.0 to 1.1.6,
    and must have today or carry as its legacy uid. rel is what those
    builds computed: the folder's resolved path relative to the resolved
    root, or the bare folder name of a child reached through a junction
    that leaves the root. Nothing else was ever yesterday's; a root that is
    itself a model and a junction child by spelling have helpers of their
    own, so a test never spells a uid no build produced as "yesterday's"."""
    return _recipe(root, rel)


def _uid_of_root_model(root: Path) -> str:
    """The uid of a root that is itself the model: rel ".". Introduced with
    finding 1; no shipped build listed a root as a model, so this uid has
    no yesterday and such a model carries a legacy uid only when a wider
    root listed it as a child."""
    return _recipe(root, ".")


def _uid_by_spelling(root: Path, rel: str) -> str:
    """The uid a child reached through a junction that leaves its root has
    TODAY: rel is the spelling the walk reached it by, relative to the root
    (finding 13). No shipped build produced it; the shipped uid of that
    folder is _uid_yesterday(root, bare name), which it carries as its
    legacy uid (finding 26)."""
    return _recipe(root, rel)


def _legacy_uid_yesterday(rel: str) -> str:
    """The uid before the root was mixed in: the relative path alone."""
    return hashlib.sha256(rel.casefold().encode("utf-8")).hexdigest()[:16]


def _no_default_root(monkeypatch, tmp_path) -> Path:
    """Point the default drop folder at a path that does not exist, so a
    scan_roots() with no argument sees only what the test configured."""
    default = tmp_path / "data" / "voice" / "models"
    monkeypatch.setattr(config, "TTS_MODELS_DIR", str(default), raising=False)
    return default


# The two cache layouts the shipped depth exists for, as the path from the
# cache's own top folder down to the folder that holds the model's files.
# HF keeps one snapshot folder per revision; ModelScope keeps the files flat
# under hub/models/<org>/<name>, no snapshots folder (the layout its own
# snapshot_download writes into ~/.cache/modelscope). The depth the scan
# needs is read off these, never retyped in a test.
HF_HOME_SNAPSHOT = Path("hub") / "models--org--voice" / "snapshots" / "rev1"
MODELSCOPE_MODEL = Path("hub") / "models" / "Org" / "Name"


# ── 1. the root itself ───────────────────────────────────────────────────────

class TestRootIsTheModel:
    def test_a_root_that_is_itself_a_model_folder_is_listed(self, tmp_path):
        root = make_xtts(tmp_path, "xtts_v2")
        res = scan_roots([root])
        assert [m.engine_id for m in res.models] == ["xtts_v2"]
        model = res.models[0]
        assert model.path == _canon(root)
        assert model.name == "xtts_v2"
        assert res.unrecognized == []

    def test_a_root_model_is_not_descended_into(self, tmp_path):
        # GROUND: the same nested folder under a plain root IS found, so the
        # absence below is the "never descend into a recognised model" rule
        # and not a broken fixture.
        plain = tmp_path / "plain"
        make_fish(plain / "inner", "deep")
        assert [m.name for m in scan_roots([plain]).models] == ["deep"]

        root = make_xtts(tmp_path, "xtts_v2")
        make_fish(root / "inner", "deep")
        assert [m.engine_id for m in scan_roots([root]).models] == ["xtts_v2"]

    def test_a_root_model_has_the_path_uid_and_no_legacy_uid(self, tmp_path):
        root = make_xtts(tmp_path, "xtts_v2")
        model = scan_roots([root]).models[0]
        # Same recipe as every other model: root key plus the path relative
        # to the root, which for the root itself is ".".
        assert model.uid == _uid_of_root_model(root)
        # No root was ever a model under the old scheme, and this root sits
        # inside no other root, so there is nothing for a stored selection to
        # be migrated from; an empty legacy uid is what keeps _resolve from
        # matching two root models to each other. The one case a root model
        # DOES carry a legacy uid is TestNestedRoots: a wider root above it.
        assert model.legacy_uid == ""

    def test_a_child_model_keeps_its_uid_when_the_root_is_not_a_model(self, tmp_path):
        """Identifying the root first must not shift the ids of everything
        under it: a model found yesterday keeps the uid it had, and the
        legacy uid a stored selection may still carry is the same as ever."""
        root = tmp_path / "models"
        make_fish(root, "s2-pro")
        model = scan_roots([root]).models[0]
        assert model.uid == _uid_yesterday(root, "s2-pro")
        assert model.legacy_uid == _legacy_uid_yesterday("s2-pro")


# ── 2. the cap is per root ───────────────────────────────────────────────────

class TestPerRootCap:
    def _two_roots(self, tmp_path):
        bloated = tmp_path / "bloated"
        for i in range(6):
            (bloated / f"d{i}").mkdir(parents=True)
        second = tmp_path / "second"
        make_fish(second, "s2-pro")
        return bloated, second

    def test_a_bloated_first_root_does_not_starve_the_second(self, tmp_path, monkeypatch):
        bloated, second = self._two_roots(tmp_path)
        # GROUND: under the shipped cap both roots are walked in full.
        res = scan_roots([bloated, second])
        assert [m.name for m in res.models] == ["s2-pro"]
        assert res.truncated is False

        monkeypatch.setattr(config, "TTS_SCAN_MAX_DIRS", 3)
        res = scan_roots([bloated, second])
        assert [m.name for m in res.models] == ["s2-pro"], (
            "the second root was never walked: one budget shared by every root"
        )

    def test_truncated_is_true_when_any_root_was_cut(self, tmp_path, monkeypatch):
        bloated, second = self._two_roots(tmp_path)
        monkeypatch.setattr(config, "TTS_SCAN_MAX_DIRS", 3)
        res = scan_roots([bloated, second])
        assert res.truncated is True
        # And the order does not matter: a cut LAST root also says so.
        res = scan_roots([second, bloated])
        assert res.truncated is True
        assert [m.name for m in res.models] == ["s2-pro"]

    def test_truncated_roots_names_each_root_that_was_cut(self, tmp_path, monkeypatch):
        """A bool cannot tell two roots apart, and the page has no way to
        let the user narrow the scan, so the sentence it shows has to name
        the folder that was cut instead."""
        bloated, second = self._two_roots(tmp_path)
        # GROUND: nothing is named under the shipped cap.
        assert scan_roots([bloated, second]).truncated_roots == []
        monkeypatch.setattr(config, "TTS_SCAN_MAX_DIRS", 3)
        assert scan_roots([bloated, second]).truncated_roots == [_canon(bloated)]
        assert scan_roots([second, bloated]).truncated_roots == [_canon(bloated)]

    def test_the_cap_counts_each_root_from_zero(self, tmp_path, monkeypatch):
        """Two roots of three directories each, cap of three: neither is cut,
        and the third directory of the second root, the model, is reached."""
        a = tmp_path / "a"
        b = tmp_path / "b"
        for r in (a, b):
            for i in range(2):
                (r / f"d{i}").mkdir(parents=True)
        (a / "d2").mkdir()
        make_fish(b, "d2")
        monkeypatch.setattr(config, "TTS_SCAN_MAX_DIRS", 3)
        res = scan_roots([a, b])
        assert res.truncated is False
        assert [m.name for m in res.models] == ["d2"]

    def test_the_models_endpoint_names_the_cut_roots(self, client, monkeypatch, tmp_path):
        root = tmp_path / "voice" / "models"
        for i in range(3):
            (root / f"d{i}").mkdir(parents=True)
        monkeypatch.setattr(config, "TTS_MODELS_DIR", str(root), raising=False)
        _write_registry([])
        # GROUND for the shape: a complete walk sends the flag and an empty list.
        body = client.get("/api/v1/tts/models").json()
        assert body["truncated"] is False and body["truncated_roots"] == []
        monkeypatch.setattr(config, "TTS_SCAN_MAX_DIRS", 1)
        body = client.get("/api/v1/tts/models").json()
        assert body["truncated"] is True
        assert body["truncated_roots"] == [_canon(root)]


# ── 4. a root that could not be read is named ────────────────────────────────

class TestUnreadableRoots:
    def test_missing_and_file_roots_are_named_not_skipped(self, tmp_path):
        missing = tmp_path / "gone"
        as_file = tmp_path / "models.txt"
        as_file.write_text("not a folder", encoding="utf-8")
        good = tmp_path / "good"
        make_fish(good, "s2-pro")

        res = scan_roots([missing, as_file, good])
        assert res.unreadable_roots == [_canon(missing), _canon(as_file)]
        # POSITIVE CONTROL: the readable root was still walked.
        assert [m.name for m in res.models] == ["s2-pro"]
        assert _canon(good) not in res.unreadable_roots

    def test_a_missing_root_still_lists_nothing_and_raises_nothing(self, tmp_path):
        """test_tts_core pins that a missing root is not an error; naming it
        must not change that."""
        res = scan_roots([tmp_path / "does-not-exist"])
        assert res.models == [] and res.unrecognized == []

    def test_a_removed_drive_is_named(self, tmp_path, monkeypatch):
        """A root on a drive letter that is not mounted. Found by walking the
        alphabet for a letter with nothing behind it; if every letter is in
        use the test has nothing to say and says so."""
        free = next(
            (f"{c}:\\" for c in "ZYXWVUTSRQPONMLKJIHGFED" if not Path(f"{c}:\\").exists()),
            None,
        )
        if free is None:
            pytest.skip("every drive letter on this machine is in use")
        gone = free + "voice-models"
        good = tmp_path / "good"
        make_fish(good, "s2-pro")
        res = scan_roots([Path(gone), good])
        assert _canon(gone) in res.unreadable_roots
        assert [m.name for m in res.models] == ["s2-pro"]

    def test_a_root_that_refuses_to_be_listed_is_named(self, tmp_path, monkeypatch):
        """Exists, is a folder, and the listing raises (an ACL somebody
        tightened, a share that dropped mid-call). Windows has no cheap way
        to build that on disk from a test, so the refusal is put in at the
        one call the scan makes, and only for that one folder."""
        locked = tmp_path / "locked"
        locked.mkdir()
        good = tmp_path / "good"
        make_fish(good, "s2-pro")
        real_iterdir = Path.iterdir

        def refusing(self):
            if self == locked:
                raise PermissionError(13, "access is denied", str(self))
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", refusing)
        res = scan_roots([locked, good])
        assert res.unreadable_roots == [_canon(locked)]
        assert [m.name for m in res.models] == ["s2-pro"]

    def test_the_default_drop_folder_not_existing_yet_is_not_reported(self, tmp_path, monkeypatch):
        """A fresh install has no voice/models folder yet. That is the normal
        state and the empty-list copy already says what to do about it, so
        it is not an unreadable root. An EXTRA root the user typed and got
        wrong is: that is the positive control."""
        default = _no_default_root(monkeypatch, tmp_path)
        _write_registry([])
        res = scan_roots()
        assert res.unreadable_roots == []
        assert _canon(default) in res.roots

        typo = tmp_path / "typo"
        _write_registry([str(typo)])
        res = scan_roots()
        assert res.unreadable_roots == [_canon(typo)]

    def test_the_default_drop_folder_as_a_file_is_reported(self, tmp_path, monkeypatch):
        """Only "does not exist yet" is the fresh state; a file in its place
        is a broken one and is said."""
        default = _no_default_root(monkeypatch, tmp_path)
        default.parent.mkdir(parents=True)
        default.write_text("x", encoding="utf-8")
        _write_registry([])
        assert scan_roots().unreadable_roots == [_canon(default)]

    def test_the_models_endpoint_carries_unreadable_roots(self, client, monkeypatch, tmp_path):
        root = tmp_path / "voice" / "models"
        root.mkdir(parents=True)
        monkeypatch.setattr(config, "TTS_MODELS_DIR", str(root), raising=False)
        typo = tmp_path / "typo"
        _write_registry([str(typo)])
        body = client.get("/api/v1/tts/models").json()
        assert body["unreadable_roots"] == [_canon(typo)]
        # GROUND for the shape: the flag the frontend must also carry.
        assert body["truncated"] is False


class TestUnlistableSubfolders:
    """A folder the walk cannot open mid-way is not a root, so it has no
    place in unreadable_roots; it used to vanish with no trace at all. One
    warning per root says HOW MANY such folders the walk met, and names
    none of them: elysium.log sits outside the vault, and the part of a path
    below the root carries the Windows account name as soon as the root sits
    above the profile folder (C:\\ or C:\\Users, which is exactly the
    mis-pointed root the config comment names), because every profile holds
    a set of legacy junctions that refuse to be listed."""

    @staticmethod
    def _refusing(*folders: Path):
        real_iterdir = Path.iterdir
        locked = set(folders)

        def refusing(self):
            if self in locked:
                raise PermissionError(13, "access is denied", str(self))
            return real_iterdir(self)

        return refusing

    @staticmethod
    def _warnings(caplog) -> list[str]:
        return [
            r.getMessage() for r in caplog.records
            if r.name == "tts.registry" and r.levelno == logging.WARNING
        ]

    def test_a_subfolder_that_refuses_to_be_listed_is_counted_not_named(
        self, tmp_path, monkeypatch, caplog,
    ):
        root = tmp_path / "models"
        locked = root / "vendor" / "locked"
        locked.mkdir(parents=True)
        make_fish(root, "s2-pro")

        # GROUND: nothing is logged while every folder can be listed.
        with caplog.at_level(logging.WARNING, logger="tts.registry"):
            scan_roots([root])
        assert self._warnings(caplog) == []

        monkeypatch.setattr(Path, "iterdir", self._refusing(locked))
        with caplog.at_level(logging.WARNING, logger="tts.registry"):
            res = scan_roots([root])
        lines = self._warnings(caplog)
        assert len(lines) == 1
        assert re.search(r"\b1\b", lines[0]), lines[0]
        for segment in ("locked", "vendor", "models", str(tmp_path)):
            assert segment not in lines[0], (segment, lines[0])
        # The rest of the walk is unaffected.
        assert [m.name for m in res.models] == ["s2-pro"]
        assert res.unreadable_roots == []

    def test_the_count_is_per_root_and_counts_every_folder(
        self, tmp_path, monkeypatch, caplog,
    ):
        """POSITIVE CONTROL for the number: two folders under one root is one
        line saying 2, and one folder under each of two roots is two lines
        saying 1, so the count is neither a constant nor a global."""
        one = tmp_path / "one"
        (one / "x").mkdir(parents=True)
        (one / "y").mkdir()
        two = tmp_path / "two"
        (two / "z").mkdir(parents=True)
        monkeypatch.setattr(
            Path, "iterdir", self._refusing(one / "x", one / "y", two / "z"),
        )
        with caplog.at_level(logging.WARNING, logger="tts.registry"):
            scan_roots([one, two])
        lines = self._warnings(caplog)
        assert len(lines) == 2
        assert re.search(r"\b2\b", lines[0]) and not re.search(r"\b1\b", lines[0])
        assert re.search(r"\b1\b", lines[1]) and not re.search(r"\b2\b", lines[1])

    def test_a_root_above_the_profile_folder_does_not_log_the_account_name(
        self, tmp_path, monkeypatch, caplog,
    ):
        """The shape a mis-pointed root has on a real machine: the root is
        the drive, and under it Users\\<account>\\Cookies is one of the
        legacy junctions every profile refuses to list. The account segment
        must not reach the log by any route, relative or absolute."""
        drive = tmp_path / "C"
        account = "AccountNameXYZ"
        cookies = drive / "Users" / account / "Cookies"
        cookies.mkdir(parents=True)
        monkeypatch.setattr(Path, "iterdir", self._refusing(cookies))
        with caplog.at_level(logging.WARNING, logger="tts.registry"):
            scan_roots([drive])
        lines = self._warnings(caplog)
        # GROUND: the refusal was met and reported at all.
        assert len(lines) == 1
        assert account not in lines[0]
        assert "Cookies" not in lines[0]
        assert "Users" not in lines[0]


# ── 5. extra_roots are normalised ────────────────────────────────────────────

class TestExtraRootNormalisation:
    def test_user_home_and_env_vars_are_expanded(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("ELYSIUM_TEST_ROOT", str(tmp_path / "envroot"))
        _write_registry([
            "~/hf-models",
            "%ELYSIUM_TEST_ROOT%/x",
            "$ELYSIUM_TEST_ROOT/y",
        ])
        assert extra_roots() == [
            _canon(home / "hf-models"),
            _canon(tmp_path / "envroot" / "x"),
            _canon(tmp_path / "envroot" / "y"),
        ]

    def test_a_relative_root_is_anchored_to_the_data_dir_not_the_cwd(self, tmp_path, monkeypatch):
        """The process working directory is the backend folder in development
        and wherever the exe was launched from in the shipped app; DATA_DIR is
        the one folder that is the same in both."""
        data = tmp_path / "data"
        monkeypatch.setattr(config, "DATA_DIR", data, raising=False)
        _no_default_root(monkeypatch, tmp_path)
        make_fish(data / "rel-models", "s2-pro")
        _write_registry(["rel-models"])
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        assert extra_roots() == [_canon(data / "rel-models")]
        assert [m.name for m in scan_roots().models] == ["s2-pro"]

    def test_the_same_root_spelled_twice_is_one_root(self, tmp_path, monkeypatch):
        root = tmp_path / "models"
        make_fish(root, "s2-pro")
        _write_registry([str(root), str(root).upper(), str(root) + os.sep])
        assert extra_roots() == [_canon(root)]

        _no_default_root(monkeypatch, tmp_path)
        res = scan_roots()
        assert [m.name for m in res.models] == ["s2-pro"]

    def test_explicit_duplicate_roots_list_a_model_once(self, tmp_path):
        root = tmp_path / "models"
        make_fish(root, "s2-pro")
        res = scan_roots([root, root])
        assert len(res.models) == 1
        assert res.roots == [_canon(root)]

    def test_normalisation_keeps_the_uid_of_a_canonical_absolute_root(self, tmp_path):
        """A model found under an absolute, canonical root before this change
        must keep its uid after it: the digest is still root key plus
        relative path, and a non-canonical spelling of the same root lands on
        the same id rather than a second one."""
        root = tmp_path / "models"
        make_fish(root, "s2-pro")
        expected = _uid_yesterday(root, "s2-pro")
        assert scan_roots([root]).models[0].uid == expected
        dotted = tmp_path / "models" / ".." / "models"
        assert scan_roots([dotted]).models[0].uid == expected
        assert scan_roots([Path(str(root).lower())]).models[0].uid == expected

    def test_roots_report_the_effective_paths(self, tmp_path, monkeypatch):
        """What the scan actually walked, after expansion, is what the client
        and the sidecar-write confinement in routers/tts.py see."""
        data = tmp_path / "data"
        monkeypatch.setattr(config, "DATA_DIR", data, raising=False)
        default = _no_default_root(monkeypatch, tmp_path)
        (data / "rel").mkdir(parents=True)
        _write_registry(["rel"])
        assert scan_roots().roots == [_canon(default), _canon(data / "rel")]

    def test_non_string_and_blank_entries_are_dropped(self, tmp_path):
        good = tmp_path / "good"
        _write_registry([str(good), "", "   ", 42, None])  # type: ignore[list-item]
        assert extra_roots() == [_canon(good)]


# ── 8. nested roots are both walked ──────────────────────────────────────────

class TestNestedRoots:
    """Two roots, one inside the other. Both are walked, and the outer walk
    steps around the inner root, so every folder is reached by exactly one
    walk, the one whose root sits nearest above it, and that root is the one
    in its uid. Whichever order the two are listed in."""

    def _nested(self, tmp_path):
        outer = tmp_path / "outer"
        inner = outer / "inner"
        make_fish(inner, "s2-pro")
        make_xtts(outer / "other", "xtts_v2")
        return outer, inner

    @pytest.mark.parametrize("order", ["outer first", "inner first"])
    def test_each_folder_is_listed_once_under_the_nearest_root(self, tmp_path, order):
        outer, inner = self._nested(tmp_path)
        roots = [outer, inner] if order == "outer first" else [inner, outer]
        res = scan_roots(roots)
        by_name = {m.name: m for m in res.models}
        assert len(res.models) == 2 and set(by_name) == {"s2-pro", "xtts_v2"}
        assert by_name["s2-pro"].uid == _uid_yesterday(inner, "s2-pro")
        assert by_name["xtts_v2"].uid == _uid_yesterday(outer, "other/xtts_v2")
        assert res.roots == [_canon(r) for r in roots]

    def test_an_inner_root_below_the_depth_limit_is_still_walked(self, tmp_path):
        """The inner root sits one level past what the outer walk reaches.
        Dropping it as "covered by the outer walk" was the silent miss."""
        outer = tmp_path / "outer"
        inner = outer
        for i in range(config.TTS_SCAN_MAX_DEPTH):
            inner = inner / f"lvl{i}"
        inner = inner / "inner"
        make_fish(inner, "s2-pro")
        # GROUND: the outer walk alone cannot reach it; the inner root alone can.
        assert scan_roots([outer]).models == []
        assert [m.name for m in scan_roots([inner]).models] == ["s2-pro"]

        res = scan_roots([outer, inner])
        assert [m.uid for m in res.models] == [_uid_yesterday(inner, "s2-pro")]
        assert res.truncated is False and res.unreadable_roots == []

    def test_an_inner_root_past_the_outer_cap_is_still_walked(self, tmp_path, monkeypatch):
        """The outer root is wider than the cap and the inner root sorts
        last, so the outer walk is cut before it gets there. The inner root
        has a budget of its own."""
        outer = tmp_path / "outer"
        for i in range(6):
            (outer / f"d{i}").mkdir(parents=True)
        inner = outer / "zz-inner"
        make_fish(inner, "s2-pro")
        monkeypatch.setattr(config, "TTS_SCAN_MAX_DIRS", 3)
        # GROUND: the outer walk alone is cut short of the folder.
        alone = scan_roots([outer])
        assert alone.models == [] and alone.truncated is True

        res = scan_roots([outer, inner])
        assert [m.uid for m in res.models] == [_uid_yesterday(inner, "s2-pro")]
        # The outer root was still cut and is still named for it; the inner
        # root was not.
        assert res.truncated is True
        assert res.truncated_roots == [_canon(outer)]

    @pytest.mark.parametrize("order", ["outer first", "inner first"])
    def test_a_root_that_is_a_model_inside_a_wider_root_carries_its_old_uid_as_legacy(
        self, tmp_path, order,
    ):
        """The user pointed extra_roots at the model folder, found nothing
        (finding 1), then added the parent and left both. The model is now
        the inner root's own row, under the inner root's uid, and the uid it
        had under the parent rides along as the legacy uid so the stored
        selection is recognised once and rewritten, instead of the chosen
        voice turning into "no model selected"."""
        outer = tmp_path / "checkpoints"
        inner = make_fish(outer, "s2-pro")
        # GROUND: under the parent alone the model has yesterday's uid, and
        # as a root on its own it has no wider root to carry an old id from.
        assert scan_roots([outer]).models[0].uid == _uid_yesterday(outer, "s2-pro")
        assert scan_roots([inner]).models[0].legacy_uid == ""

        roots = [outer, inner] if order == "outer first" else [inner, outer]
        res = scan_roots(roots)
        assert len(res.models) == 1
        model = res.models[0]
        assert model.uid == _uid_of_root_model(inner)
        assert model.legacy_uid == _uid_yesterday(outer, "s2-pro")

    def test_the_legacy_uid_of_a_root_model_comes_from_the_nearest_root_above_it(
        self, tmp_path,
    ):
        """Three roots nested three deep, the innermost a model: its old uid
        is the one it had under the MIDDLE root, which is what [outer,
        middle] listed it as yesterday, not the one under the outermost."""
        outer = tmp_path / "outer"
        middle = outer / "middle"
        inner = make_fish(middle, "s2-pro")
        assert scan_roots([outer, middle]).models[0].uid == _uid_yesterday(middle, "s2-pro")
        model = scan_roots([outer, middle, inner]).models[0]
        assert model.uid == _uid_of_root_model(inner)
        assert model.legacy_uid == _uid_yesterday(middle, "s2-pro")

    def test_the_selection_follows_a_model_that_became_its_own_root(
        self, client, monkeypatch, tmp_path,
    ):
        """Through the real migration in routers/tts_runtime._resolve, with
        the drop folder as the wider root: the model was selected while it
        was a child of the drop folder, then its own folder was added as an
        extra root. The selection resolves to the same folder and is written
        down under the new uid, so the migration runs once."""
        import routers.tts_runtime as runtime
        from database import get_setting, set_setting

        default = _no_default_root(monkeypatch, tmp_path)
        make_xtts(default, "xtts_v2")
        _write_registry([])
        before = runtime.scan_roots().models[0]
        assert before.uid == _uid_yesterday(default, "xtts_v2")
        set_setting(runtime.SETTING_ACTIVE_UID, before.uid)

        _write_registry([str(default / "xtts_v2")])
        after = runtime.scan_roots().models[0]
        assert after.uid != before.uid, "the inner root did not take the model"

        resolved = runtime._resolve(None)
        assert resolved.uid == after.uid
        assert resolved.path == _canon(default / "xtts_v2")
        assert get_setting(runtime.SETTING_ACTIVE_UID) == after.uid, (
            "the migration has to be written down, or it runs on every call"
        )

    # The shape the four migration tests below start from: the model was
    # selected while it was a child of the drop folder, then its own folder
    # was added as an extra root, so the saved selection names the old uid.

    def _selected_as_a_child(self, monkeypatch, tmp_path) -> tuple[Path, str]:
        import routers.tts as tts_router
        from database import set_setting

        default = _no_default_root(monkeypatch, tmp_path)
        make_xtts(default, "xtts_v2")
        _write_registry([])
        old = scan_roots().models[0].uid
        assert old == _uid_yesterday(default, "xtts_v2")
        set_setting(tts_router.SETTING_ACTIVE_UID, old)
        return default, old

    @staticmethod
    def _made_its_own_root(default: Path) -> str:
        _write_registry([str(default / "xtts_v2")])
        new = scan_roots().models[0].uid
        assert new == _uid_of_root_model(default / "xtts_v2")
        return new

    def test_the_active_endpoint_follows_the_selection_and_rewrites_it_once(
        self, client, monkeypatch, tmp_path,
    ):
        """Only _resolve followed a legacy uid. GET /tts/active matched the
        uid alone, so until a speak or a load ran, the chat was told the old
        uid with state "error" and no readiness (it drew Speak as ready) and
        the settings page found no row to mark selected. The rewrite runs
        once: the second read finds the new uid directly and writes nothing."""
        import routers.tts as tts_router
        from database import get_setting
        from tts.errors import TTS_MODEL_UNKNOWN

        default, old = self._selected_as_a_child(monkeypatch, tmp_path)
        new = self._made_its_own_root(default)
        selection_writes: list[str] = []
        real_set_setting = tts_router.set_setting

        def counting(key: str, value: str) -> None:
            if key == tts_router.SETTING_ACTIVE_UID:
                selection_writes.append(value)
            real_set_setting(key, value)

        monkeypatch.setattr(tts_router, "set_setting", counting)

        body = client.get("/api/v1/tts/active").json()
        assert body["uid"] == new
        assert body["state"] == "unloaded" and body["engine_id"] == "xtts_v2"
        assert body["readiness"] is not None
        assert body["error_code"] != TTS_MODEL_UNKNOWN
        assert get_setting(tts_router.SETTING_ACTIVE_UID) == new
        assert selection_writes == [new]

        assert client.get("/api/v1/tts/active").json()["uid"] == new
        assert selection_writes == [new], (
            "the rewrite ran again on a selection that already held the new uid"
        )

    def test_a_settings_url_with_the_old_uid_finds_the_model_and_leaves_the_selection_alone(
        self, client, monkeypatch, tmp_path,
    ):
        """_find matched the uid alone as well, so a page still holding the
        old uid got 400 from every settings and readiness route. A uid from
        the URL is not the saved selection and rewrites nothing; a save made
        through the old uid lands under the model's uid, where the read is."""
        import routers.tts as tts_router
        from database import get_setting
        from tts.errors import TTS_MODEL_UNKNOWN

        default, old = self._selected_as_a_child(monkeypatch, tmp_path)
        new = self._made_its_own_root(default)
        assert client.get(f"/api/v1/tts/models/{old}/readiness").status_code == 200
        r = client.post(f"/api/v1/tts/models/{old}/settings",
                        json={"values": {"temperature": 0.9}})
        assert r.status_code == 200 and r.json()["values"]["temperature"] == 0.9
        assert client.get(f"/api/v1/tts/models/{new}/settings").json()["values"]["temperature"] == 0.9
        assert get_setting(tts_router.SETTING_ACTIVE_UID) == old
        # GROUND: a uid nothing ever had is still the contract error.
        r = client.get("/api/v1/tts/models/nope/readiness")
        assert r.status_code == 400 and r.json()["detail"] == TTS_MODEL_UNKNOWN

    def test_the_saved_values_follow_the_selection_to_its_new_uid(
        self, client, monkeypatch, tmp_path,
    ):
        """The values row is keyed by uid too, and the rewrite moved the
        selection alone: the voice the user had tuned came back at its
        defaults. The row is copied on the same rewrite and never deleted,
        so a client still holding the old uid reads what it saved."""
        import routers.tts as tts_router
        import routers.tts_runtime as runtime
        from database import get_setting

        default, old = self._selected_as_a_child(monkeypatch, tmp_path)
        r = client.post(f"/api/v1/tts/models/{old}/settings",
                        json={"values": {"temperature": 0.9}})
        assert r.status_code == 200 and r.json()["values"]["temperature"] == 0.9
        saved_row = get_setting(tts_router._settings_key(old))
        new = self._made_its_own_root(default)
        # GROUND: nothing sits under the new uid before the rewrite.
        assert get_setting(tts_router._settings_key(new)) is None

        assert runtime._resolve(None).uid == new
        assert get_setting(tts_router._settings_key(new)) == saved_row
        assert get_setting(tts_router._settings_key(old)) == saved_row
        body = client.get(f"/api/v1/tts/models/{new}/settings").json()
        assert body["values"]["temperature"] == 0.9
        assert body["source_map"]["temperature"] == "saved"

    def test_values_already_saved_under_the_new_uid_are_not_overwritten(
        self, client, monkeypatch, tmp_path,
    ):
        """The settings page can save against the new uid before the chat
        next reads the selection (the list shows the new uid after a
        rescan). That save is the newer one and the rewrite keeps it."""
        default, old = self._selected_as_a_child(monkeypatch, tmp_path)
        client.post(f"/api/v1/tts/models/{old}/settings",
                    json={"values": {"temperature": 0.9}})
        new = self._made_its_own_root(default)
        r = client.post(f"/api/v1/tts/models/{new}/settings",
                        json={"values": {"temperature": 0.5}})
        assert r.status_code == 200
        assert client.get("/api/v1/tts/active").json()["uid"] == new
        body = client.get(f"/api/v1/tts/models/{new}/settings").json()
        assert body["values"]["temperature"] == 0.5

    def test_an_inner_root_keeps_its_own_unrecognised_rows(self, tmp_path):
        """A folder with files above the inner root is a row of the outer
        walk; it does not swallow the rows of a root the user listed on
        purpose. The ground is the plain collapse: under ONE root a folder
        below a listed folder is not a second row."""
        outer = tmp_path / "outer"
        _touch(outer / "a" / "readme.txt")
        _touch(outer / "a" / "b" / "c" / "config.json", b"{}")
        assert [u.path for u in scan_roots([outer]).unrecognized] == [_canon(outer / "a")]

        inner = outer / "a" / "b"
        listed = [u.path for u in scan_roots([outer, inner]).unrecognized]
        assert listed == [_canon(outer / "a"), _canon(inner / "c")]


# ── 9. the drop folder is a container ────────────────────────────────────────

class TestDropFolderIsAContainer:
    def test_loose_files_in_the_drop_folder_do_not_hide_the_folders_under_it(
        self, tmp_path, monkeypatch,
    ):
        default = _no_default_root(monkeypatch, tmp_path)
        make_fish(default.parent, default.name)   # fish's files unzipped straight into it
        make_xtts(default, "xtts_v2")             # and a proper folder beside them
        _write_registry([])
        res = scan_roots()
        assert [(m.engine_id, m.name) for m in res.models] == [("xtts_v2", "xtts_v2")]
        assert _canon(default) not in [m.path for m in res.models]
        # POSITIVE CONTROL: the very same folder handed over as a root the
        # user chose IS the model, and nothing under it is looked at. Only
        # the drop folder is exempt, because the app defines it as the place
        # model FOLDERS go.
        alone = scan_roots([default])
        assert [(m.engine_id, m.name) for m in alone.models] == [("fish_s2", "models")]


# ── 15. a root that holds files no engine claims is a row ────────────────────

class TestRootWithLooseFiles:
    """A Piper or Kokoro folder pointed at directly: it holds files, matches
    no engine, and used to be neither a model nor an unrecognised row nor a
    hint of any kind. It is judged the way any child folder is judged."""

    def _piper(self, parent: Path) -> Path:
        piper = parent / "piper-voice"
        _touch(piper / "en_US-amy-medium.onnx")
        _touch(piper / "en_US-amy-medium.onnx.json", b"{}")
        return piper

    def test_a_root_with_files_that_match_no_engine_is_an_unrecognised_row(self, tmp_path):
        piper = self._piper(tmp_path)
        # GROUND: one level up, the very same folder is a row already.
        assert [u.path for u in scan_roots([tmp_path]).unrecognized] == [_canon(piper)]

        res = scan_roots([piper])
        assert res.models == []
        assert [u.path for u in res.unrecognized] == [_canon(piper)]
        assert res.unreadable_roots == []

    def test_a_root_with_files_above_a_model_is_a_container_not_a_row(self, tmp_path):
        """POSITIVE CONTROL for the rule that lists it: a folder on the way
        to a model is not an error, root or not. The HF hub keeps a
        version.txt at its top and holds the models below it."""
        hub = tmp_path / "hub"
        _touch(hub / "version.txt", b"1")
        make_xtts(hub / "models--org--voice" / "snapshots", "rev1")
        res = scan_roots([hub])
        assert [m.engine_id for m in res.models] == ["xtts_v2"]
        assert res.unrecognized == []

    def test_a_root_with_files_is_the_row_only_when_nothing_below_it_is(self, tmp_path):
        """The hub keeps a version.txt at its top. Pointed at as a root with
        one LLM snapshot below it and no voice model, the row yesterday was
        the snapshot, the folder the user has to look at; judging the root
        first made the root itself the row and absorbed the snapshot. The
        root row is a fallback for a root with nothing listable under it
        (the Piper folder above is the ground for that), never the first
        row of its own walk."""
        hub = tmp_path / "hub"
        _touch(hub / "version.txt", b"1")
        llm = hub / "models--org--llm"
        snap = llm / "snapshots" / "rev2"
        _touch(snap / "config.json", b"{}")
        _touch(snap / "model.safetensors")
        _touch(llm / "refs" / "main", b"rev2")
        _touch(llm / "blobs" / "abc")
        res = scan_roots([hub])
        assert res.models == []
        assert [u.path for u in res.unrecognized] == [_canon(snap)]

    def test_a_local_dir_download_is_the_row_not_its_cache_folder(self, tmp_path):
        """huggingface-cli --local-dir writes .cache/huggingface/download
        bookkeeping INSIDE the model folder. With the root row a fallback,
        that bookkeeping was the row for a downloaded model no engine
        claims; it is HF's own folder and is neither listed nor entered,
        so the model folder is the row, pointed at directly or from above."""
        local_dir = tmp_path / "local-dir"
        _touch(local_dir / "config.json", b"{}")
        _touch(local_dir / "model.safetensors")
        _touch(local_dir / ".cache" / "huggingface" / ".gitignore", b"*")
        _touch(local_dir / ".cache" / "huggingface" / "download" / "config.json.metadata", b"x")
        assert [u.path for u in scan_roots([local_dir]).unrecognized] == [_canon(local_dir)]
        assert [u.path for u in scan_roots([tmp_path]).unrecognized] == [_canon(local_dir)]

    def test_hf_home_with_a_token_file_lists_the_hub_below_it_not_itself(self, tmp_path):
        """HF_HOME holds a token file at its top and the hub under it. The
        row is the hub, the highest folder with files under the root that
        has rows of its own, the same as it was before roots were judged."""
        hf = tmp_path / "huggingface"
        _touch(hf / "token", b"hf_xxx")
        _touch(hf / "hub" / "version.txt", b"1")
        _touch(hf / "hub" / "models--org--llm" / "snapshots" / "rev" / "config.json", b"{}")
        assert [u.path for u in scan_roots([hf]).unrecognized] == [_canon(hf / "hub")]

    def test_a_root_pointed_at_directly_is_the_container_of_the_rows_under_it(self, tmp_path):
        """Kokoro keeps its voices in a subfolder of .pt files. Pointed at
        from one level up, the model folder is the row and voices/ is not a
        second one. Pointed at directly, the root is the container its walk
        runs in and voices/ is the row, the answer the scan gave before
        roots were judged at all: the root row is a fallback for a root
        with nothing to list under it (the Piper folder above), never the
        first row of its own walk. The root first made a hub with a
        version.txt the row and hid the snapshot under it, and nothing in
        the files tells a Kokoro folder from a cache."""
        kokoro = tmp_path / "Kokoro-82M"
        _touch(kokoro / "config.json", b"{}")
        _touch(kokoro / "kokoro-v1_0.pth")
        _touch(kokoro / "voices" / "af_heart.pt")
        assert [u.path for u in scan_roots([tmp_path]).unrecognized] == [_canon(kokoro)]
        assert [u.path for u in scan_roots([kokoro]).unrecognized] == [_canon(kokoro / "voices")]

    @pytest.mark.parametrize("order", ["hub first", "inner first"])
    @pytest.mark.parametrize("below", ["a folder no engine claims", "a model"])
    def test_a_root_with_files_is_a_container_when_an_inner_root_lists_what_is_below_it(
        self, tmp_path, order, below,
    ):
        """[hub, hub/inner] with a version.txt at the top of hub. The walk of
        hub stepped around inner, found nothing of its own, and hub was a
        row BESIDE inner's row, where [hub] alone listed inner's folder and
        never hub. Something listed under the root by any walk makes the
        root a container, whichever order the roots are given in. The
        model variant is the CONTROL: a folder on the way to a model was
        already a container through the model-container rule, and the two
        rules must agree on the answer."""
        hub = tmp_path / "hub"
        _touch(hub / "version.txt", b"1")
        inner = hub / "inner"
        if below == "a model":
            make_fish(inner, "c")
        else:
            _touch(inner / "c" / "config.json", b"{}")

        def rows(res) -> list[str]:
            return [m.path for m in res.models] + [u.path for u in res.unrecognized]

        # GROUND: the wider root alone lists the folder below and not itself.
        assert rows(scan_roots([hub])) == [_canon(inner / "c")]

        roots = [hub, inner] if order == "hub first" else [inner, hub]
        assert rows(scan_roots(roots)) == [_canon(inner / "c")]

    def test_a_root_with_files_stays_the_row_when_the_inner_root_lists_nothing(self, tmp_path):
        """POSITIVE CONTROL for the rule above: an inner root with nothing
        to list leaves the wider root the row it was on its own."""
        hub = tmp_path / "hub"
        _touch(hub / "version.txt", b"1")
        inner = hub / "inner"
        inner.mkdir()
        assert [u.path for u in scan_roots([hub]).unrecognized] == [_canon(hub)]
        for roots in ([hub, inner], [inner, hub]):
            res = scan_roots(roots)
            assert res.models == []
            assert [u.path for u in res.unrecognized] == [_canon(hub)]

    def test_the_drop_folder_with_loose_files_is_still_not_a_row(self, tmp_path, monkeypatch):
        """The one root that is never judged: the app defines it as the place
        model FOLDERS go, so files dropped loose in it are not a row about
        the drop folder itself. Its subfolders are judged as ever."""
        default = _no_default_root(monkeypatch, tmp_path)
        _touch(default / "stray.onnx")
        _touch(default / "odd" / "config.json", b"{}")
        _write_registry([])
        res = scan_roots()
        assert [u.path for u in res.unrecognized] == [_canon(default / "odd")]


# ── 10. one real folder is one row ───────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are a Windows reparse point")
class TestJunctions:
    @pytest.fixture
    def junction(self):
        """A directory junction, which an ordinary Windows user may create; a
        symlink would need a privilege the test runner may not hold. Removed
        with rmdir, which takes the junction and never what it points at.
        Each removal stands alone, so one that fails (a handle still open on
        it, a link a test removed itself) cannot leave the others behind in
        tmp_path; the failures are raised together once every link has been
        tried."""
        import _winapi

        made: list[Path] = []

        def make(target: Path, link: Path) -> Path:
            _winapi.CreateJunction(str(target), str(link))
            made.append(link)
            return link

        yield make
        failed: list[tuple[Path, OSError]] = []
        for link in made:
            try:
                os.rmdir(link)
            except FileNotFoundError:
                continue      # a test removed it itself
            except OSError as exc:
                failed.append((link, exc))
        if failed:
            raise OSError("junction(s) left behind: " + "; ".join(
                f"{link.name}: {exc}" for link, exc in failed))

    @pytest.mark.parametrize("order", ["a first", "b first"])
    def test_a_junction_into_a_sibling_root_leaves_the_model_to_that_root(
        self, tmp_path, junction, order,
    ):
        """a/link -> b, and both are roots. The first walk to reach b/s2-pro
        used to keep it: with a listed first the row was a/link/s2-pro under
        a's uid, which depended on the root order and on a junction in an
        unrelated root, and the uid the model had under b yesterday was
        gone with no legacy uid. The nearest configured root above the real
        folder is b, whichever walk arrives first."""
        a = tmp_path / "a"
        a.mkdir()
        b = tmp_path / "b"
        make_fish(b, "s2-pro")
        junction(b, a / "link")
        # GROUND: through the junction alone the model is found, so a single
        # row below is attribution and not a junction the walk could not
        # enter.
        assert [m.name for m in scan_roots([a]).models] == ["s2-pro"]

        roots = [a, b] if order == "a first" else [b, a]
        res = scan_roots(roots)
        assert [m.path for m in res.models] == [_canon(b / "s2-pro")]
        assert res.models[0].uid == _uid_yesterday(b, "s2-pro")

    def test_a_junction_in_the_drop_folder_into_an_extra_root_keeps_that_root_uid(
        self, tmp_path, monkeypatch, junction,
    ):
        """The drop folder is always the first root. drop/link ->
        store/xtts_v2 with store as an extra root listed the model once as
        drop/link under the drop folder's uid; the uid it had under store was
        gone and carried no legacy uid, so a selection saved under it
        answered 400."""
        default = _no_default_root(monkeypatch, tmp_path)
        default.mkdir(parents=True)
        store = tmp_path / "store"
        make_xtts(store, "xtts_v2")
        junction(store / "xtts_v2", default / "link")
        _write_registry([str(store)])
        # GROUND: under store alone the model has the uid it had yesterday.
        expected = _uid_yesterday(store, "xtts_v2")
        assert scan_roots([store]).models[0].uid == expected

        res = scan_roots()
        assert [m.path for m in res.models] == [_canon(store / "xtts_v2")]
        assert res.models[0].uid == expected

    @pytest.mark.parametrize("order", ["R first", "S first"])
    def test_a_junction_to_the_parent_leaves_a_sibling_root_to_its_own_walk(
        self, tmp_path, junction, order,
    ):
        """P/{R, S}, R/aaa -> P, the model at S/s2-pro, both R and S roots.
        Through aaa the walk of R reached P/S/s2-pro and, R listed first,
        kept it as R/aaa/S/s2-pro under R's uid. S is the nearest configured
        root above the real folder, and its walk is the one that lists it."""
        parent = tmp_path / "P"
        r = parent / "R"
        r.mkdir(parents=True)
        s = parent / "S"
        make_fish(s, "s2-pro")
        junction(parent, r / "aaa")
        roots = [r, s] if order == "R first" else [s, r]
        res = scan_roots(roots)
        assert [m.path for m in res.models] == [_canon(s / "s2-pro")]
        assert res.models[0].uid == _uid_yesterday(s, "s2-pro")

    @pytest.mark.parametrize("order", ["outer first", "inner first"])
    @pytest.mark.parametrize("link_depth", [1, 2])
    def test_a_folder_below_a_junction_is_left_to_the_inner_root(
        self, tmp_path, junction, order, link_depth,
    ):
        """outer/mid/inner is a model and a root of its own; a junction at
        depth 1 (outer/aaa) or depth 2 (outer/x/aaa) points at outer/mid.
        Only the child that IS the junction used to be compared by where it
        goes: outer/aaa resolves to mid, which is no root, so it was entered,
        and outer/aaa/inner is an ordinary folder whose spelling matched
        nothing, so the outer walk listed the model under digest(outer,
        "mid/inner") and the inner root got no row. Every child is compared
        by its canonical path once more than one root is configured."""
        outer = tmp_path / "outer"
        mid = outer / "mid"
        inner = make_fish(mid, "inner")
        link_parent = outer if link_depth == 1 else outer / "x"
        link_parent.mkdir(parents=True, exist_ok=True)
        junction(mid, link_parent / "aaa")
        roots = [outer, inner] if order == "outer first" else [inner, outer]
        res = scan_roots(roots)
        assert [m.path for m in res.models] == [_canon(inner)]
        assert res.models[0].uid == _uid_of_root_model(inner)
        assert res.models[0].legacy_uid == _uid_yesterday(outer, "mid/inner")

    def test_a_junction_to_the_parent_counts_its_revisits_against_the_cap(
        self, tmp_path, monkeypatch, junction,
    ):
        """root/aaa -> the root's own parent. It is followed, since that is
        how a model behind such a junction is found at all, and every
        revisit of the root through it (root/aaa/root, root/aaa/root/aaa,
        and on to the depth limit) counts against the root's directory
        cap; the cut is reported like any other. Pinned so the cost stays
        written down: the scan docstring says it in one sentence, and this
        is the shape it describes."""
        root = tmp_path / "root"
        for i in range(5):
            (root / f"d{i}").mkdir(parents=True)
        make_fish(root, "zz-model")
        monkeypatch.setattr(config, "TTS_SCAN_MAX_DIRS", 12)
        # GROUND: six real folders fit the cap with room to spare.
        plain = scan_roots([root])
        assert plain.truncated is False
        assert [m.name for m in plain.models] == ["zz-model"]

        junction(root.parent, root / "aaa")
        res = scan_roots([root])
        assert res.truncated_roots == [_canon(root)]
        assert [m.path for m in res.models] == [_canon(root / "zz-model")]

    def test_a_junction_into_a_folder_that_is_no_root_is_still_entered_with_two_roots(
        self, tmp_path, junction,
    ):
        """POSITIVE CONTROL for the canonical check: it steps around other
        roots and nothing else. a/link -> d, d no root, c a second unrelated
        root: the model behind the junction is still a's row, by the path
        the walk reached it, exactly as with a alone, and it carries the
        uid every shipped build gave it as its legacy either way."""
        a = tmp_path / "a"
        a.mkdir()
        c = tmp_path / "c"
        c.mkdir()
        d = tmp_path / "d"
        make_fish(d, "s2-pro")
        junction(d, a / "link")
        expected = [(_uid_by_spelling(a, "link/s2-pro"), _uid_yesterday(a, "s2-pro"))]
        assert [(m.uid, m.legacy_uid) for m in scan_roots([a]).models] == expected
        assert [(m.uid, m.legacy_uid) for m in scan_roots([a, c]).models] == expected

    def test_a_junction_back_into_its_own_root_lists_each_folder_once(self, tmp_path, junction):
        """models/self -> models listed models/m at every depth the walk
        reached, all under ONE uid (the relative path resolves through the
        junction), and an unrecognised folder the same way."""
        root = tmp_path / "models"
        make_fish(root, "s2-pro")
        _touch(root / "odd" / "config.json", b"{}")
        junction(root, root / "self")
        res = scan_roots([root])
        assert [m.path for m in res.models] == [_canon(root / "s2-pro")]
        assert [u.path for u in res.unrecognized] == [_canon(root / "odd")]

    def test_two_junctions_to_two_folders_of_one_name_are_two_uids(self, tmp_path, junction):
        """c/ext1 -> d1 and c/ext2 -> d2, each holding an xtts_v2 folder. The
        resolved path of either escapes the root, and the fallback was the
        bare folder name, so two real folders were two rows under ONE uid: a
        duplicate React key, shared settings, and the first row loaded
        whichever one was picked. The uid is the path the walk reached the
        folder by, relative to the root, which is unique per folder. The
        one uid both had in every shipped build is the legacy of both, and
        a selection saved under it resolves to the first row, the one those
        builds loaded."""
        from routers.tts import model_for_uid

        c = tmp_path / "c"
        c.mkdir()
        make_xtts(tmp_path / "d1", "xtts_v2")
        make_xtts(tmp_path / "d2", "xtts_v2")
        junction(tmp_path / "d1", c / "ext1")
        junction(tmp_path / "d2", c / "ext2")
        res = scan_roots([c])
        assert sorted(Path(m.path).parent.name for m in res.models) == ["ext1", "ext2"]
        uids = {m.uid for m in res.models}
        assert len(uids) == 2, "two real folders collided onto one uid"
        assert uids == {
            _uid_by_spelling(c, "ext1/xtts_v2"),
            _uid_by_spelling(c, "ext2/xtts_v2"),
        }
        shipped = _uid_yesterday(c, "xtts_v2")
        assert [m.legacy_uid for m in res.models] == [shipped, shipped]
        assert model_for_uid(shipped, res.models).path == str(Path(_canon(c)) / "ext1" / "xtts_v2")

    def test_a_junction_sibling_does_not_take_the_uid_of_the_real_folder(self, tmp_path, junction):
        """a/s2-pro is a real folder and a/link -> b holds a second s2-pro.
        The real folder keeps exactly the uid it had yesterday (the resolved
        path never escaped the root, so nothing about it changed), and the
        one reached through the junction is a different uid, not a copy. In
        every shipped build the two collided onto the real folder's uid;
        the junction row carries that uid as its legacy, and a selection
        saved under it still resolves to the real folder, which is the row
        those builds loaded (a live uid wins over a legacy one)."""
        from routers.tts import model_for_uid

        a = tmp_path / "a"
        make_fish(a, "s2-pro")
        b = tmp_path / "b"
        make_fish(b, "s2-pro")
        junction(b, a / "link")
        res = scan_roots([a])
        by_path = {Path(m.path).parent.name: m for m in res.models}
        assert set(by_path) == {"a", "link"}
        assert by_path["a"].uid == _uid_yesterday(a, "s2-pro")
        assert by_path["a"].legacy_uid == _legacy_uid_yesterday("s2-pro")
        assert by_path["link"].uid == _uid_by_spelling(a, "link/s2-pro")
        assert by_path["link"].legacy_uid == _uid_yesterday(a, "s2-pro")
        assert by_path["a"].uid != by_path["link"].uid
        picked = model_for_uid(_uid_yesterday(a, "s2-pro"), res.models)
        assert picked is by_path["a"]
        assert model_for_uid(_uid_yesterday(a, "s2-pro"), res.models[::-1]) is by_path["a"]

    @pytest.mark.parametrize("order", ["outer first", "inner first"])
    def test_a_junction_into_the_inner_root_is_stepped_around_too(self, tmp_path, junction, order):
        """outer/aaa-link -> outer/inner, and both are roots. The step-around
        compared the lexical child key against the inner root's canonical
        key, so the outer walk went through the junction, reached the model
        first and listed it under the OUTER root's uid; the inner root, the
        nearest one above the model, never got its row. A child that is a
        reparse point is compared by where it really goes as well."""
        outer = tmp_path / "outer"
        inner = outer / "inner"
        make_fish(inner, "s2-pro")
        junction(inner, outer / "aaa-link")
        roots = [outer, inner] if order == "outer first" else [inner, outer]
        res = scan_roots(roots)
        assert [m.path for m in res.models] == [_canon(inner / "s2-pro")]
        assert res.models[0].uid == _uid_yesterday(inner, "s2-pro")

    def test_a_junction_child_that_leaves_the_root_remembers_its_shipped_uid(
        self, tmp_path, junction,
    ):
        """models/j -> ext2, the model at ext2/xtts_v2. Every shipped build
        gave it digest(models, "xtts_v2"), the bare name; the fix for the
        two-junctions collision moved its uid onto the spelling and gave it
        digest("", "j/xtts_v2") as its legacy, which no build ever produced,
        so the saved selection had nothing to be found by. The shipped uid
        is the legacy. GROUND: a child reached by its real path in the same
        root keeps the legacy from before roots were mixed in."""
        root = tmp_path / "models"
        make_fish(root, "s2-pro")
        ext2 = tmp_path / "ext2"
        make_xtts(ext2, "xtts_v2")
        junction(ext2, root / "j")
        by_name = {m.name: m for m in scan_roots([root]).models}
        assert by_name["s2-pro"].uid == _uid_yesterday(root, "s2-pro")
        assert by_name["s2-pro"].legacy_uid == _legacy_uid_yesterday("s2-pro")

        linked = by_name["xtts_v2"]
        assert linked.uid == _uid_by_spelling(root, "j/xtts_v2")
        assert linked.legacy_uid == _uid_yesterday(root, "xtts_v2")
        assert linked.legacy_uid != _legacy_uid_yesterday("j/xtts_v2")

    def test_a_selection_saved_by_a_shipped_build_behind_a_junction_is_followed_and_rewritten(
        self, client, monkeypatch, junction, tmp_path,
    ):
        """The 1.1.6 user: models\\hf -> D:\\hf, hf\\xtts_v2 selected, then
        the update. Through the real GET /tts/active: the saved uid is the
        shipped one, the answer is the model under its uid today with its
        readiness, not tts_model_unknown, and the selection is written down
        under the new uid."""
        import routers.tts as tts_router
        from database import get_setting, set_setting
        from tts.errors import TTS_MODEL_UNKNOWN

        default = _no_default_root(monkeypatch, tmp_path)
        default.mkdir(parents=True)
        store = tmp_path / "hf-store"
        make_xtts(store, "xtts_v2")
        junction(store, default / "hf")
        _write_registry([])
        shipped = _uid_yesterday(default, "xtts_v2")
        today = _uid_by_spelling(default, "hf/xtts_v2")
        assert [m.uid for m in scan_roots().models] == [today]
        set_setting(tts_router.SETTING_ACTIVE_UID, shipped)

        body = client.get("/api/v1/tts/active").json()
        assert body["uid"] == today
        assert body["state"] == "unloaded" and body["engine_id"] == "xtts_v2"
        assert body["readiness"] is not None
        assert body["error_code"] != TTS_MODEL_UNKNOWN
        assert get_setting(tts_router.SETTING_ACTIVE_UID) == today

    @pytest.mark.parametrize("order", ["wide first", "link first"])
    @pytest.mark.parametrize("rel", ["jlink", "sub/jlink"])
    def test_a_junction_root_inside_a_wider_root_remembers_the_uid_it_had_under_it(
        self, tmp_path, junction, order, rel,
    ):
        """[wide, wide/jlink] with jlink -> elsewhere/model. The root above
        a root was found by canonical key only, and elsewhere/model sits
        inside no root, so the model carried no legacy uid at all, although
        [wide] alone listed it at wide/jlink. The uid it had under wide is
        by its spelling relative to wide: at depth 1 that is the bare name,
        the shipped recipe as well; at depth 2 the spelling."""
        wide = tmp_path / "wide"
        wide.mkdir()
        model = make_fish(tmp_path / "elsewhere", "model")
        link = wide / rel
        link.parent.mkdir(parents=True, exist_ok=True)
        junction(model, link)
        # GROUND: the wider root alone lists the folder through the link.
        alone = scan_roots([wide]).models[0]
        assert alone.path == str(Path(_canon(wide)) / rel)
        assert alone.uid == _uid_by_spelling(wide, rel)
        if rel == "jlink":
            assert alone.uid == _uid_yesterday(wide, "jlink")

        roots = [wide, link] if order == "wide first" else [link, wide]
        res = scan_roots(roots)
        assert [m.path for m in res.models] == [_canon(model)]
        assert res.models[0].uid == _uid_of_root_model(model)
        assert res.models[0].legacy_uid == alone.uid

    @pytest.mark.parametrize("order", ["a first", "b first"])
    def test_a_root_with_files_whose_only_child_is_a_junction_into_another_root_is_not_a_row(
        self, tmp_path, junction, order,
    ):
        """a holds notes.txt and a/link -> b, the model at b/m, both roots.
        The walk of a handed the link to b's walk, found nothing of its own,
        and a was a "not recognised" row, where [a] alone listed the model
        behind the link with a as its container. What b's walk lists sits
        under a through the link, so a is a container here as well."""
        a = tmp_path / "a"
        _touch(a / "notes.txt", b"x")
        b = tmp_path / "b"
        make_fish(b, "m")
        junction(b, a / "link")
        alone = scan_roots([a])
        assert [m.path for m in alone.models] == [str(Path(_canon(a)) / "link" / "m")]
        assert alone.unrecognized == []

        roots = [a, b] if order == "a first" else [b, a]
        res = scan_roots(roots)
        assert [m.path for m in res.models] == [_canon(b / "m")]
        assert res.unrecognized == []

    def test_the_engine_override_reaches_a_model_behind_a_junction(
        self, client, monkeypatch, junction, tmp_path,
    ):
        """models/hf -> store, the model at store/voice. The scan lists it;
        the override resolved the folder, found it outside every root and
        answered 400 with the code for a uid nobody has. The folder is
        confined as the scan spelled it, and the sidecar lands in the folder
        the scan read. The link check on the sidecar's own name is the guard
        against a planted link and stays (test_tts_api, TestEngineOverride)."""
        default = _no_default_root(monkeypatch, tmp_path)
        default.mkdir(parents=True)
        store = tmp_path / "store"
        make_fish(store, "voice")
        junction(store, default / "hf")
        _write_registry([])
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        assert uid == _uid_by_spelling(default, "hf/voice")
        r = client.post(f"/api/v1/tts/models/{uid}/engine", json={"engine_id": "xtts_v2"})
        assert r.status_code == 200, r.json()
        side = json.loads((store / "voice" / "elysium-model.json").read_text(encoding="utf-8"))
        assert side["engine_id"] == "xtts_v2"
        listed = client.get("/api/v1/tts/models").json()["models"][0]
        assert listed["engine_id"] == "xtts_v2" and listed["source"] == "sidecar"


# ── 6. HuggingFace bookkeeping is neither listed nor entered ─────────────────

class TestHfCacheNoise:
    def _hub(self, tmp_path) -> Path:
        hub = tmp_path / "hub"
        tts = hub / "models--org--voice"
        make_xtts(tts / "snapshots", "rev1")
        _touch(tts / "blobs" / "deadbeef")
        _touch(tts / "refs" / "main")
        _touch(tts / "trees" / "rev1.json", b"{}")
        _touch(tts / ".no_exist" / "rev1" / "custom_generate")
        _touch(hub / ".locks" / "models--org--voice" / "a.lock")
        _touch(hub / "xet" / "logs" / "xet.log")
        # A snapshot that holds real files and matches no engine: listing it
        # is honest, and it is the positive control for the list being read.
        llm = hub / "models--org--llm"
        _touch(llm / "snapshots" / "rev2" / "config.json", b"{}")
        _touch(llm / "snapshots" / "rev2" / "model.safetensors")
        _touch(llm / ".no_exist" / "rev2" / "custom_generate")
        return hub

    def test_bookkeeping_folders_do_not_appear_as_unrecognised(self, tmp_path):
        hub = self._hub(tmp_path)
        res = scan_roots([hub])
        assert [m.engine_id for m in res.models] == ["xtts_v2"]
        listed = [u.path for u in res.unrecognized]
        assert listed == [_canon(hub / "models--org--llm" / "snapshots" / "rev2")]

    def test_bookkeeping_folders_are_not_entered_at_all(self, tmp_path):
        """`.no_exist` was suppressed by name and then walked, which is how
        `.no_exist/<rev>` reached the list. A model planted inside one is
        the sharpest way to say "not entered"."""
        hub = tmp_path / "hub"
        make_fish(hub / "models--x" / ".no_exist", "planted")
        make_fish(hub / ".locks", "planted2")
        make_fish(hub / "models--y" / ".cache", "planted3")
        # GROUND: the same folder under a name that is not bookkeeping is found.
        make_fish(hub / "models--x" / "extras", "seen")
        assert [m.name for m in scan_roots([hub]).models] == ["seen"]

    @pytest.mark.parametrize("name", sorted(_HF_BOOKKEEPING))
    def test_a_model_folder_named_like_bookkeeping_is_still_identified(self, tmp_path, name):
        """The registry's note says the name check comes AFTER identification,
        so a folder that IS a model is found whatever it is called. This is
        the test that goes red if the check moves above identify_dir. The
        ground is the test above: the same name as a plain container is
        neither listed nor entered."""
        root = tmp_path / "models"
        make_fish(root, name)
        res = scan_roots([root])
        assert [(m.engine_id, m.name) for m in res.models] == [("fish_s2", name)]
        assert res.unrecognized == []


class TestUnrecognisedRows:
    def test_a_folder_under_a_listed_folder_is_not_a_second_row(self, tmp_path):
        """Depth 4 exposed this on a real cache: a sentence-transformers
        snapshot was listed, and so was its 1_Pooling subfolder, which holds
        a config.json of its own. The parent row already names the folder
        the user has to look at; a row for each folder below it is noise.
        The walk is breadth first, so a parent is always considered before
        its children and the highest unrecognised folder is the one kept."""
        root = tmp_path / "models"
        snap = root / "models--org--st" / "snapshots" / "rev"
        _touch(snap / "config.json", b"{}")
        _touch(snap / "1_Pooling" / "config.json", b"{}")
        listed = [u.path for u in scan_roots([root]).unrecognized]
        assert listed == [_canon(snap)]

    def test_a_folder_whose_parent_holds_no_files_is_still_its_own_row(self, tmp_path):
        """POSITIVE CONTROL for the collapse: only a LISTED parent absorbs
        its children. A parent with no files of its own is not a row, so the
        child keeps its row, which is the shape a/model-ish/ has when a/ is
        just a folder somebody made to keep things in."""
        root = tmp_path / "models"
        _touch(root / "keep" / "model-ish" / "config.json", b"{}")
        listed = [u.path for u in scan_roots([root]).unrecognized]
        assert listed == [_canon(root / "keep" / "model-ish")]


# ── 7. depth ─────────────────────────────────────────────────────────────────

class TestDepth:
    def test_hf_home_as_a_root_reaches_the_snapshot(self, tmp_path):
        """HF_HOME/hub/models--X/snapshots/<rev> is one level past the old
        limit of 3, which stopped short and said nothing."""
        hf_home = tmp_path / "huggingface"
        make_xtts(hf_home / HF_HOME_SNAPSHOT.parent, HF_HOME_SNAPSHOT.name)
        res = scan_roots([hf_home])
        assert [m.engine_id for m in res.models] == ["xtts_v2"]
        # `hub` holds files of its own in a real cache and is on the way to
        # the model, so it must not be listed either.
        _touch(hf_home / "hub" / "version.txt", b"1")
        res = scan_roots([hf_home])
        assert res.unrecognized == []

    def test_modelscope_cache_as_a_root_reaches_the_model(self, tmp_path):
        """~/.cache/modelscope/hub/models/<org>/<name>, files flat, sits at
        the same depth as the HF snapshot."""
        ms = tmp_path / "modelscope"
        make_fish(ms / MODELSCOPE_MODEL.parent, MODELSCOPE_MODEL.name)
        assert [m.engine_id for m in scan_roots([ms]).models] == ["fish_s2"]

    def test_the_shipped_depth_is_what_those_layouts_need(self):
        needed = max(len(HF_HOME_SNAPSHOT.parts), len(MODELSCOPE_MODEL.parts))
        assert config.TTS_SCAN_MAX_DEPTH >= needed
