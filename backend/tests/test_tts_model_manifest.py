"""Q-28 - a file that is THERE but half-downloaded is still a broken model.

`readiness.py` has always said, in a comment, that it exists because "an
interrupted download looks exactly like a working model until it is loaded".
It did not do it: the only question anyone asked was `is_file()`, and a
zero-byte `model.pth` answers that yes. The user met the fault at load time,
as a stack trace, which is the exact outcome the module promises to prevent.

The missing piece was the expected size. `tts/manifest.py` is where it now
comes from: the downloader records what it wrote, `readiness.py` compares.

Two rules are load-bearing here and both get their own tests:

  * NO MANIFEST MEANS NO CLAIM. Every model already on a user's disk predates
    the manifest. The truncated-file check must be invisible to those, or a
    strengthening turns into "your working install is now refused".
  * The manifest sits in a folder the user dropped in, so its contents are
    untrusted text and its keys must never be allowed to name a file outside
    the model folder.

These are behavioural: real folders on a real temp disk, run through the real
scan and the real verdict. Nothing greps a source file.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

import config
from tts import manifest, readiness, registry, runtimes, vram
from tts.errors import TTS_MODEL_INCOMPLETE
from tts.manifest import (
    MANIFEST_NAME,
    MANIFEST_VERSION,
    MAX_ENTRIES,
    MAX_KEY_CHARS,
    MAX_KEY_PARTS,
)
from tts.readiness import ACTION_REDOWNLOAD, BLOCKER, MAX_NAMED_FILES

WEIGHTS = b"W" * 4096          # stands in for model.pth; any size will do

#: Written as a name rather than typed inline, because a literal NUL in a
#: source file makes the file binary to every tool that reads it.
NUL = "\x00"


# ── the machine around the model, so nothing else is the blocker ────────────

@pytest.fixture
def voice_root(monkeypatch, tmp_path):
    """A models root of our own, an NVIDIA card, and a registered runtime.

    The GPU and the runtime are faked so that TTS_MODEL_INCOMPLETE is the ONLY
    thing a verdict in this file can be blocked by. Without them every
    assertion below would pass on a machine with no card for the wrong reason.
    """
    root = tmp_path / "models"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_MODELS_DIR", str(root), raising=False)
    monkeypatch.setattr(
        vram, "_run_smi",
        lambda: "NVIDIA GeForce RTX 5080, 16303, 14000, 2303\n",
    )
    reg = tmp_path / "voice" / "runtimes.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(reg), raising=False)
    monkeypatch.setattr(config, "TTS_ENVS_DIR", str(tmp_path / "envs"),
                        raising=False)
    exe = tmp_path / "envs" / "xtts_v2" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    runtimes.register("xtts_v2", str(exe))
    return root


def make_model(root, name="aurora"):
    """A complete XTTS-v2 folder: config.json {"model": "xtts"} + weights."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(
        json.dumps({"model": "xtts", "languages": ["en"]}), encoding="utf-8"
    )
    (d / "model.pth").write_bytes(WEIGHTS)
    (d / "vocab.json").write_bytes(b"{}")
    return d


def record(model_dir, *names):
    """What a downloader does at the moment its fetch is known complete."""
    return manifest.write(model_dir, names or ("config.json", "model.pth",
                                               "vocab.json"))


def verdict(model_dir):
    """The real verdict, reached the real way: scan the root, then evaluate."""
    scan = registry.scan_roots()
    model = next(m for m in scan.models if m.path == str(model_dir))
    return readiness.evaluate(model)


def codes(r):
    return {i.code for i in r.issues}


def incomplete_details(r):
    return [i.detail for i in r.issues if i.code == TTS_MODEL_INCOMPLETE]


# ── the defect itself ───────────────────────────────────────────────────────

class TestAHalfDownloadedFileIsCaught:
    def test_a_zero_byte_weights_file_that_still_exists_is_incomplete(
        self, voice_root
    ):
        """The defect, exactly. `model.pth` is STILL THERE - is_file() says yes,
        `missing` is empty - and it holds nothing."""
        d = make_model(voice_root)
        record(d)
        (d / "model.pth").write_bytes(b"")
        assert (d / "model.pth").is_file(), "the point is that it still exists"

        r = verdict(d)
        assert TTS_MODEL_INCOMPLETE in codes(r)
        assert r.runnable is False
        assert any("model.pth" in detail for detail in incomplete_details(r))

    def test_a_complete_folder_with_the_same_manifest_says_nothing(
        self, voice_root
    ):
        """POSITIVE CONTROL. Without this the test above would pass on any
        folder at all, including a perfectly healthy one."""
        d = make_model(voice_root)
        record(d)
        r = verdict(d)
        assert TTS_MODEL_INCOMPLETE not in codes(r)
        assert r.runnable is True

    def test_half_a_file_is_caught_not_only_an_empty_one(self, voice_root):
        """4096 bytes recorded, 2048 on disk. A download does not have to stop
        at zero to be useless."""
        d = make_model(voice_root)
        record(d)
        (d / "model.pth").write_bytes(WEIGHTS[: len(WEIGHTS) // 2])
        assert TTS_MODEL_INCOMPLETE in codes(verdict(d))

    def test_a_file_that_grew_is_caught_too(self, voice_root):
        """The test is DIFFERS, not smaller. Bytes that are not the bytes that
        were fetched are not the model, whichever direction they went."""
        d = make_model(voice_root)
        record(d)
        (d / "model.pth").write_bytes(WEIGHTS + b"more")
        assert TTS_MODEL_INCOMPLETE in codes(verdict(d))

    def test_a_recorded_file_that_vanished_is_caught(self, voice_root):
        """Deleting a file the adapter does not require leaves `missing` empty,
        so the manifest is the only witness."""
        d = make_model(voice_root)
        (d / "speakers_xtts.pth").write_bytes(b"S" * 128)
        record(d, "config.json", "model.pth", "vocab.json", "speakers_xtts.pth")
        (d / "speakers_xtts.pth").unlink()

        r = verdict(d)
        assert TTS_MODEL_INCOMPLETE in codes(r)
        assert any("speakers_xtts.pth" in detail
                   for detail in incomplete_details(r))

    def test_deleting_a_legitimately_empty_file_is_still_caught(
        self, voice_root
    ):
        """A file recorded as 0 bytes and then deleted used to read as 0 bytes
        and match its own record, so the loss went unreported. Absence has to
        be a different answer from emptiness, not the same one."""
        d = make_model(voice_root)
        (d / "marker.bin").write_bytes(b"")
        record(d, "config.json", "model.pth", "vocab.json", "marker.bin")
        assert manifest.read(d)["marker.bin"] == 0      # ground: recorded as 0
        assert manifest.short_files(d) == ()            # ground: intact, quiet

        (d / "marker.bin").unlink()
        assert manifest.short_files(d) == ("marker.bin",)

    def test_a_file_we_are_not_allowed_to_measure_is_not_accused(
        self, voice_root, monkeypatch
    ):
        """The sentence this check produces costs the user a multi-gigabyte
        re-download. A permission error, a locked file or a drive that blinked
        is not evidence of a bad download, so it buys silence, not a verdict."""
        d = make_model(voice_root)
        record(d)
        real_stat = Path.stat

        def refuse(self, *args, **kwargs):
            if self.name == "model.pth":
                raise PermissionError("access is denied")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", refuse)
        assert manifest.short_files(d) == ()

    def test_being_unmeasurable_excuses_only_itself(self, voice_root,
                                                    monkeypatch):
        """GROUND CONTROL for the silence above: one unreadable file must not
        buy the whole folder an amnesty."""
        d = make_model(voice_root)
        record(d)
        (d / "vocab.json").write_bytes(b"{}{}{}")
        real_stat = Path.stat

        def refuse(self, *args, **kwargs):
            if self.name == "model.pth":
                raise PermissionError("access is denied")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", refuse)
        assert manifest.short_files(d) == ("vocab.json",)

    def test_the_issue_sends_the_user_to_the_same_button_as_a_missing_file(
        self, voice_root
    ):
        d = make_model(voice_root)
        record(d)
        (d / "model.pth").write_bytes(b"")
        issue = next(i for i in verdict(d).issues
                     if i.code == TTS_MODEL_INCOMPLETE)
        assert issue.severity == BLOCKER
        assert issue.action == ACTION_REDOWNLOAD
        assert issue.transient is False      # re-checking will not fix bytes

    def test_a_truncated_file_is_not_described_as_missing(self, voice_root):
        """The user can SEE model.pth in the folder. Telling them it is missing
        sends them looking for something that is right there."""
        d = make_model(voice_root)
        record(d)
        (d / "model.pth").write_bytes(b"")
        details = incomplete_details(verdict(d))
        assert details, "the fault must be reported at all"
        assert not any(dt.startswith("missing from the model folder")
                       for dt in details)

    def test_a_file_in_a_subfolder_is_checked(self, voice_root):
        """HF snapshot layouts nest, so a flat-only check would miss most of a
        real download."""
        d = make_model(voice_root)
        (d / "extra").mkdir()
        (d / "extra" / "shard.bin").write_bytes(b"S" * 512)
        record(d, "config.json", "model.pth", "vocab.json", "extra/shard.bin")
        (d / "extra" / "shard.bin").write_bytes(b"")
        assert TTS_MODEL_INCOMPLETE in codes(verdict(d))


# ── no manifest means no claim ──────────────────────────────────────────────

class TestModelsThatPredateTheManifest:
    def test_a_folder_with_no_manifest_is_left_exactly_as_it_was(
        self, voice_root
    ):
        """GROUND CONTROL for the whole feature, and the rule it must not
        break: every model already on a user's disk has no manifest. Even a
        zero-byte weights file must not become a refusal for them, because
        this check is a strengthening for future downloads, not a new veto
        over an install that has been speaking for months."""
        d = make_model(voice_root)
        (d / "model.pth").write_bytes(b"")
        assert not (d / MANIFEST_NAME).exists()

        r = verdict(d)
        assert TTS_MODEL_INCOMPLETE not in codes(r)
        assert r.runnable is True

    def test_the_very_same_folder_is_refused_once_a_manifest_arrives(
        self, voice_root
    ):
        """POSITIVE CONTROL for the silence above, and it has to be this shape.

        Calling a healthy no-manifest folder "runnable" proves only that the
        fixture works. The claim being guarded is that the SILENCE comes from
        the absent manifest and nothing else, so the same broken folder is
        measured twice: mute, then, with only a manifest added, refused."""
        d = make_model(voice_root)
        (d / "model.pth").write_bytes(b"")
        assert TTS_MODEL_INCOMPLETE not in codes(verdict(d))

        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {"model.pth": len(WEIGHTS)}}),
            encoding="utf-8",
        )
        assert TTS_MODEL_INCOMPLETE in codes(verdict(d))

    def test_a_manifest_from_a_future_version_makes_no_claim(self, voice_root):
        """An older build meeting a newer file must say nothing rather than
        guess at a shape it does not know."""
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION + 1,
                        "files": {"model.pth": 999999}}),
            encoding="utf-8",
        )
        (d / "model.pth").write_bytes(b"")
        assert TTS_MODEL_INCOMPLETE not in codes(verdict(d))

    @pytest.mark.parametrize("body", [
        "this is not json at all",
        json.dumps({"version": MANIFEST_VERSION}),                 # no files
        json.dumps({"version": MANIFEST_VERSION, "files": []}),    # wrong type
        json.dumps({"files": {"model.pth": 1}}),                   # no version
        json.dumps({"version": MANIFEST_VERSION,
                    "files": {"model.pth": "4096"}}),              # size is text
        json.dumps({"version": MANIFEST_VERSION,
                    "files": {"model.pth": -1}}),                  # negative
        json.dumps({"version": MANIFEST_VERSION,
                    "files": {"model.pth": True}}),                # bool is int
        json.dumps({"version": True, "files": {"model.pth": 1}}),  # True == 1
        json.dumps({"version": 1.0, "files": {"model.pth": 1}}),   # 1.0 == 1
    ])
    def test_junk_in_the_manifest_makes_no_claim_and_does_not_raise(
        self, voice_root, body
    ):
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(body, encoding="utf-8")
        (d / "model.pth").write_bytes(b"")
        r = verdict(d)                                   # must not raise
        assert TTS_MODEL_INCOMPLETE not in codes(r)

    def test_one_bad_entry_does_not_switch_the_check_off_for_the_others(
        self, voice_root
    ):
        """GROUND CONTROL against over-correcting the rule above: a single
        unreadable line must not buy the whole folder an amnesty."""
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {"vocab.json": "nonsense",
                                  "model.pth": len(WEIGHTS)}}),
            encoding="utf-8",
        )
        (d / "model.pth").write_bytes(b"")
        assert TTS_MODEL_INCOMPLETE in codes(verdict(d))

    def test_a_manifest_over_the_entry_cap_makes_no_claim(self, voice_root):
        """A number in a user-supplied file must not decide how many stats the
        settings page performs."""
        d = make_model(voice_root)
        # Exactly one over, not two: a cap loosened by a single entry has to
        # fail this, or the boundary is not the thing being measured.
        files = {f"f{i}.bin": 1 for i in range(MAX_ENTRIES)}
        files["model.pth"] = len(WEIGHTS)
        assert len(files) == MAX_ENTRIES + 1
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION, "files": files}),
            encoding="utf-8",
        )
        (d / "model.pth").write_bytes(b"")
        assert TTS_MODEL_INCOMPLETE not in codes(verdict(d))

    def test_the_cap_is_not_so_tight_it_refuses_a_real_download(
        self, voice_root
    ):
        """POSITIVE CONTROL for the cap, sat exactly ON it: a manifest of
        precisely MAX_ENTRIES still counts, so the refusal above is the
        boundary and not a blanket."""
        d = make_model(voice_root)
        files = {f"f{i}.bin": 0 for i in range(MAX_ENTRIES - 1)}
        files["model.pth"] = len(WEIGHTS)
        assert len(files) == MAX_ENTRIES
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION, "files": files}),
            encoding="utf-8",
        )
        (d / "model.pth").write_bytes(b"")
        assert TTS_MODEL_INCOMPLETE in codes(verdict(d))


# ── the manifest is untrusted input ─────────────────────────────────────────

class TestTheFolderIsUntrusted:
    # Two of these are WINDOWS SPELLINGS and are refused because Windows path
    # semantics split them: on POSIX `Path("..\\outside.txt").parts` is one
    # ordinary filename and the entry would be kept. That is not a hole here -
    # Elysium ships Windows-only (pywebview shell, .exe artefact, the whole
    # suite runs on Scripts\python.exe) - but a reader porting this file
    # elsewhere has to know the two marked lines lean on the platform.
    @pytest.mark.parametrize("key", [
        "../outside.txt",
        "..\\outside.txt",              # Windows spelling
        "sub/../../outside.txt",
        "C:\\Windows\\win.ini",         # Windows spelling
        "/etc/passwd",
    ])
    def test_a_key_that_leads_out_of_the_model_folder_is_ignored(
        self, voice_root, key
    ):
        """Stat-ing whatever a planted key names would turn the settings page
        into a file-existence oracle for the rest of the disk."""
        d = make_model(voice_root)
        (voice_root / "outside.txt").write_bytes(b"not ours")
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION, "files": {key: 99999}}),
            encoding="utf-8",
        )
        r = verdict(d)                                   # must not raise
        assert TTS_MODEL_INCOMPLETE not in codes(r)
        assert manifest.read(d) == {}

    def test_a_wandering_key_does_not_excuse_the_honest_one_beside_it(
        self, voice_root
    ):
        """POSITIVE CONTROL: the refusal above is per entry, not per file."""
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {"../outside.txt": 1,
                                  "model.pth": len(WEIGHTS)}}),
            encoding="utf-8",
        )
        (d / "model.pth").write_bytes(b"")
        assert TTS_MODEL_INCOMPLETE in codes(verdict(d))

    def test_a_key_that_wanders_and_comes_back_is_refused_too(self, voice_root):
        """`extra/../model.pth` names a file that IS inside the folder, so the
        confinement alone would wave it through. It is still refused: one file
        spelled two ways is a manifest nobody can reason about, and nothing we
        write produces it."""
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {"extra/../model.pth": len(WEIGHTS)}}),
            encoding="utf-8",
        )
        assert manifest.read(d) == {}

    def test_a_network_path_is_refused_before_anything_resolves_it(
        self, voice_root, monkeypatch
    ):
        """resolve() on \\\\host\\share reaches for the network, and the
        settings page must not block on a machine somebody named in a JSON
        file. So this asserts the ORDER, not only the outcome: the name never
        reaches a syscall at all."""
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {"\\\\evil-host\\share\\x.bin": 1,
                                  "model.pth": len(WEIGHTS)}}),
            encoding="utf-8",
        )
        seen: list[str] = []
        real_resolve = Path.resolve

        def spy(self, *args, **kwargs):
            seen.append(str(self))
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", spy)
        # GROUND CONTROL, and it has to be in the same read: the honest key
        # beside it DOES get resolved, so `seen` proves the spy is live and
        # that the confinement really runs on ordinary entries. Asserting only
        # that `seen` is non-empty would be satisfied by the folder's own
        # resolve, which happens before any entry is looked at.
        assert manifest.read(d) == {"model.pth": len(WEIGHTS)}
        assert any(path.endswith("model.pth") for path in seen), \
            "the honest entry was never resolved, so this proves nothing"
        assert not any("evil-host" in path for path in seen)

    def test_a_junction_out_of_the_folder_is_refused(self, voice_root,
                                                     tmp_path):
        """The third refusal, the one the first two cannot reach: a key with
        no `..` and no drive that is nonetheless a door out, because somebody
        put a directory junction in the model folder. A junction needs no
        privilege to create, unlike a symlink, so this is the reachable form.

        Without resolving the target, `escape/win.ini` reads as an ordinary
        relative name and the size behind it would be measured."""
        d = make_model(voice_root)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "secret.bin").write_bytes(b"not ours at all")
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(d / "escape"), str(elsewhere)],
            capture_output=True, text=True,
        )
        if made.returncode != 0:                     # no junction, no claim
            pytest.skip(f"could not create a junction: {made.stderr.strip()}")
        if (d / "escape").resolve() == (d / "escape"):
            pytest.skip("this filesystem did not follow the junction")

        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {"escape/secret.bin": 99999,
                                  "model.pth": len(WEIGHTS)}}),
            encoding="utf-8",
        )
        # GROUND CONTROL: the junction really does lead somewhere, so the
        # refusal below is the confinement and not a broken path.
        assert (d / "escape" / "secret.bin").is_file()
        assert manifest.read(d) == {"model.pth": len(WEIGHTS)}

    def test_a_junk_key_cannot_buy_the_whole_folder_an_amnesty(
        self, voice_root
    ):
        """FAIL-OPEN, the worst direction available here.

        `Path.stat` raises ValueError, not OSError, on a key with an embedded
        NUL. That escaped the per-entry loop into the blanket handler, so ONE
        junk key silenced the check for every other file in the folder - in the
        exact scenario this module exists for."""
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {"a" + NUL + "b": 5,
                                  "model.pth": len(WEIGHTS)}}),
            encoding="utf-8",
        )
        (d / "model.pth").write_bytes(b"")
        assert manifest.short_files(d) == ("model.pth",)

    @pytest.mark.parametrize("key", [
        "model.pth ",              # Windows strips the trailing space
        "model.pth.",              # and the trailing dot
        "model.pth:evil",          # an NTFS alternate data stream
        "model.pth::$DATA",        # the same file's data by another name
        ".",                       # the model folder itself
        "  ",                      # the model folder again, wearing a hat
    ])
    def test_a_key_that_is_not_a_plain_name_in_this_folder_is_ignored(
        self, voice_root, key
    ):
        """Each of these stats something real and then reports a name the user
        cannot find in their folder. Windows resolves them; a person reading
        the blocker cannot."""
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION, "files": {key: 999}}),
            encoding="utf-8",
        )
        assert manifest.read(d) == {}

    def test_a_deep_key_cannot_stall_the_settings_page(self, voice_root):
        """Capping the entry COUNT did not cap the cost of ONE entry.

        `resolve()` is priced by how many components a path has, so keys a few
        thousand deep cost seconds each. Twenty of them - a file one twelfth of
        the size the reader accepts, at one twelfth of the entry cap - stalled
        a single model for twenty-eight seconds, and the settings page walks
        every model on every render.

        A budget, not a shape: the assertion is on the clock, because the fault
        was a cost and any fix that is not cheap has not fixed it."""
        d = make_model(voice_root)
        deep = "/".join(["a"] * 8000) + "/x.bin"
        files = {f"{deep}{i}": 1 for i in range(20)}
        files["model.pth"] = len(WEIGHTS)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION, "files": files}),
            encoding="utf-8",
        )
        (d / "model.pth").write_bytes(b"")

        start = time.perf_counter()
        short = manifest.short_files(d)
        spent = time.perf_counter() - start

        assert spent < 1.0, f"one model took {spent:.1f}s of a page render"
        # GROUND CONTROL: it was not fast because it gave up on the folder.
        assert short == ("model.pth",)

    @pytest.mark.parametrize("key", [
        "/".join(["a"] * (MAX_KEY_PARTS + 1)),      # too deep
        "b" * (MAX_KEY_CHARS + 1),                  # too long
        "NUL", "nul.bin", "COM1", "sub/CON",        # Windows devices, not files
    ])
    def test_a_key_that_is_not_shaped_like_a_model_file_is_ignored(
        self, voice_root, key
    ):
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION, "files": {key: 999}}),
            encoding="utf-8",
        )
        assert manifest.read(d) == {}

    def test_an_ordinary_nested_name_still_counts(self, voice_root):
        """GROUND CONTROL for both caps: the shapes a real download produces
        are nowhere near them, so the refusals above are not a blanket."""
        d = make_model(voice_root)
        nested = d / "snapshots" / "abc123"
        nested.mkdir(parents=True)
        (nested / "model-00001-of-00002.safetensors").write_bytes(b"S" * 64)
        record(d, "model.pth",
               "snapshots/abc123/model-00001-of-00002.safetensors")
        assert manifest.short_files(d) == ()
        (nested / "model-00001-of-00002.safetensors").write_bytes(b"")
        assert manifest.short_files(d) == (
            "snapshots/abc123/model-00001-of-00002.safetensors",)

    def test_the_named_files_in_one_issue_are_bounded(self, voice_root):
        """The names come out of a JSON file in a folder the user dropped in,
        so the payload must not carry an unbounded quantity of somebody else's
        text. Two hundred file names is also not a sentence anyone reads."""
        d = make_model(voice_root)
        for i in range(40):
            (d / f"part{i:03d}.bin").write_bytes(b"P")
        record(d, *[f"part{i:03d}.bin" for i in range(40)])
        for i in range(40):
            (d / f"part{i:03d}.bin").write_bytes(b"")

        detail = incomplete_details(verdict(d))[0]
        assert detail.count("part") == MAX_NAMED_FILES
        assert f"and {40 - MAX_NAMED_FILES} more" in detail

    def test_a_short_list_is_still_spelled_out_in_full(self, voice_root):
        """GROUND CONTROL for the cap: the ordinary case names every file, so
        the bound above is a bound and not a permanent truncation."""
        d = make_model(voice_root)
        record(d)
        (d / "model.pth").write_bytes(b"")
        (d / "vocab.json").write_bytes(b"{}{}")
        detail = incomplete_details(verdict(d))[0]
        assert "model.pth" in detail and "vocab.json" in detail
        assert "more" not in detail

    def test_one_file_spelled_two_ways_makes_no_claim_at_all(self, voice_root):
        """NTFS resolves `model.pth` and `MODEL.PTH` to the same bytes, so a
        manifest carrying both says one file has two sizes. There is no winner
        to pick, and picking one accused a correct file under a name that is
        not even on disk. Incoherent in, silence out."""
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {"model.pth": len(WEIGHTS),
                                  "MODEL.PTH": 999}}),
            encoding="utf-8",
        )
        assert manifest.read(d) is None
        assert manifest.short_files(d) == ()

    def test_a_dot_slash_prefix_is_the_same_file_and_is_caught(
        self, voice_root
    ):
        """`./model.pth` and `model.pth` are one file, and comparing the raw
        strings did not notice. The collision test keys on the NORMALISED
        path, or the two-spellings rule has a hole shaped like a prefix."""
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {"model.pth": len(WEIGHTS),
                                  "./model.pth": 999}}),
            encoding="utf-8",
        )
        assert manifest.read(d) is None

    def test_two_genuinely_different_files_are_not_mistaken_for_one(
        self, voice_root
    ):
        """GROUND CONTROL for the refusal above: it keys on the FILE, not on
        the spelling looking unusual."""
        d = make_model(voice_root)
        record(d)
        (d / "model.pth").write_bytes(b"")
        assert manifest.short_files(d) == ("model.pth",)

    def test_the_manifest_cannot_be_made_to_measure_itself(self, voice_root):
        """The self-reference skip is case-insensitive for the same reason
        everything else here is: `Elysium-Download.json` walked past a `==`
        and had the file reporting on its own size."""
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {"Elysium-Download.json": 1}}),
            encoding="utf-8",
        )
        assert manifest.read(d) == {}

    def test_a_junction_that_loops_back_to_the_folder_is_refused(
        self, voice_root
    ):
        """The confinement is STRICTLY inside, and this is why that word is
        there. A junction pointing at its own parent resolves to the model
        folder itself, and "inside or equal to" accepted it - so a manifest
        could record a size for a DIRECTORY and the verdict would say a folder
        is not the size it was downloaded at. There is no file to check."""
        d = make_model(voice_root)
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(d / "loop"), str(d)],
            capture_output=True, text=True,
        )
        if made.returncode != 0:
            pytest.skip(f"could not create a junction: {made.stderr.strip()}")
        if (d / "loop").resolve() != d.resolve():
            pytest.skip("this filesystem did not follow the junction")

        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {"loop": 99999,
                                  "model.pth": len(WEIGHTS)}}),
            encoding="utf-8",
        )
        # GROUND CONTROL: the loop is real and does lead back to the folder.
        assert (d / "loop" / "model.pth").is_file()
        assert manifest.read(d) == {"model.pth": len(WEIGHTS)}

    def test_the_manifest_never_reports_on_itself(self, voice_root):
        """It cannot record its own size - the number changes when the number
        is written - so an entry naming it is noise, not a fault."""
        d = make_model(voice_root)
        (d / MANIFEST_NAME).write_text(
            json.dumps({"version": MANIFEST_VERSION,
                        "files": {MANIFEST_NAME: 1}}),
            encoding="utf-8",
        )
        assert manifest.short_files(d) == ()

    def test_a_folder_that_is_not_there_is_silence_not_an_error(self, tmp_path):
        assert manifest.read(tmp_path / "nope") is None
        assert manifest.short_files(tmp_path / "nope") == ()


# ── writing it ──────────────────────────────────────────────────────────────

class TestTheDownloaderSide:
    def test_what_is_written_is_what_is_read_back(self, tmp_path):
        d = make_model(tmp_path)
        manifest.write(d, ["config.json", "model.pth", "vocab.json"])
        got = manifest.read(d)
        assert got == {
            "config.json": (d / "config.json").stat().st_size,
            "model.pth": len(WEIGHTS),
            "vocab.json": 2,
        }

    def test_it_refuses_to_certify_a_file_that_is_not_there_yet(self, tmp_path):
        """The whole contract. A manifest written before the download finishes
        would record the truncated size as correct, and the check downstream
        would then vouch for exactly the folder it exists to catch."""
        d = make_model(tmp_path)
        with pytest.raises(FileNotFoundError):
            manifest.write(d, ["model.pth", "not-downloaded-yet.bin"])
        assert not (d / MANIFEST_NAME).exists(), \
            "a partial manifest is worse than none"

    def test_it_refuses_a_path_that_leaves_the_model_folder(self, tmp_path):
        d = make_model(tmp_path)
        with pytest.raises(ValueError):
            manifest.write(d, ["../elsewhere.bin"])
        assert not (d / MANIFEST_NAME).exists()

    def test_rewriting_leaves_no_temporary_file_behind(self, tmp_path):
        """It is written beside the weights, in a folder the user browses. A
        litter of .tmp files there reads as a broken app."""
        d = make_model(tmp_path)
        manifest.write(d, ["model.pth"])
        manifest.write(d, ["model.pth", "vocab.json"])
        assert [p.name for p in d.glob("*.tmp")] == []
        assert set(manifest.read(d)) == {"model.pth", "vocab.json"}

    def test_a_freshly_written_manifest_reports_nothing_short(self, tmp_path):
        """GROUND CONTROL for the writer: the two halves have to agree, or the
        check fires on every download the moment it lands."""
        d = make_model(tmp_path)
        manifest.write(d, ["config.json", "model.pth", "vocab.json"])
        assert manifest.short_files(d) == ()

    def test_it_refuses_to_name_one_file_twice(self, tmp_path):
        """Our own writer must not be able to emit a file our own reader throws
        away. `read` refuses a manifest whose keys collide under Windows
        casing, so producing one here would be a silent way to ship a manifest
        that checks nothing."""
        d = make_model(tmp_path)
        with pytest.raises(ValueError):
            manifest.write(d, ["model.pth", "MODEL.PTH"])
        assert not (d / MANIFEST_NAME).exists()

    def test_two_real_files_are_still_allowed(self, tmp_path):
        """GROUND CONTROL: the refusal above is about one file named twice,
        not about writing more than one entry."""
        d = make_model(tmp_path)
        manifest.write(d, ["model.pth", "vocab.json"])
        assert set(manifest.read(d)) == {"model.pth", "vocab.json"}

    def test_a_folder_is_not_a_file_and_says_so(self, tmp_path):
        """"is not there" about a name that is plainly there sends the caller
        after the wrong thing."""
        d = make_model(tmp_path)
        (d / "shards").mkdir()
        with pytest.raises(IsADirectoryError):
            manifest.write(d, ["shards"])
        assert not (d / MANIFEST_NAME).exists()

    def test_a_write_that_fails_midway_leaves_no_litter(self, tmp_path,
                                                        monkeypatch):
        """The failure path, not the happy one. The temp file lands in the
        user's own model folder, so a crash between mkstemp and replace must
        not leave a stray .tmp sitting next to the weights."""
        d = make_model(tmp_path)

        def boom(*args, **kwargs):
            raise RuntimeError("the disk filled up")

        monkeypatch.setattr(json, "dump", boom)
        with pytest.raises(RuntimeError):
            manifest.write(d, ["model.pth"])
        assert [p.name for p in d.glob("*.tmp")] == []
        assert not (d / MANIFEST_NAME).exists()


# ── it must not break the verdict it lives in ───────────────────────────────

class TestItNeverMakesThingsWorse:
    def test_both_kinds_of_incomplete_arrive_as_one_issue(self, voice_root):
        """VoiceSettingsPage renders the blocker list with `key={issue.code}`
        and prints getErrorMessage(code), never the detail. Two issues sharing
        this code would be a duplicate React key AND the same sentence printed
        twice, so one file absent and another truncated must still be one
        Issue - carrying both facts in the words the developer reads."""
        d = make_model(voice_root)
        (d / "speakers_xtts.pth").write_bytes(b"S" * 128)
        record(d, "config.json", "model.pth", "vocab.json", "speakers_xtts.pth")
        (d / "vocab.json").unlink()                  # a REQUIRED file, gone
        (d / "speakers_xtts.pth").write_bytes(b"")   # present, truncated

        details = incomplete_details(verdict(d))
        assert len(details) == 1, "one code, one issue, or React sees a dup key"
        assert "vocab.json" in details[0]
        assert "speakers_xtts.pth" in details[0]

    def test_a_file_that_is_both_required_and_gone_is_named_once(
        self, voice_root
    ):
        """The two halves overlap and the overlap has to be subtracted.

        A deleted `vocab.json` is reported by BOTH: `missing`, because the
        adapter requires it, and the manifest, because zero bytes is not the
        size recorded. Printed as-is the sentence named it twice and the second
        clause called a deleted file "present" - the exact wrong errand this
        wording exists to avoid."""
        d = make_model(voice_root)
        record(d)
        (d / "vocab.json").unlink()

        detail = incomplete_details(verdict(d))[0]
        assert detail.count("vocab.json") == 1
        assert "present but not the size" not in detail

    def test_the_subtraction_does_not_swallow_a_second_real_fault(
        self, voice_root
    ):
        """GROUND CONTROL for that subtraction: only the overlapping name is
        removed, and a genuinely truncated file beside it still gets said."""
        d = make_model(voice_root)
        record(d)
        (d / "vocab.json").unlink()
        (d / "model.pth").write_bytes(b"")

        detail = incomplete_details(verdict(d))[0]
        assert "missing from the model folder: vocab.json" in detail
        assert "present but not the size the download recorded: model.pth" \
            in detail

    def test_an_unreadable_descriptor_does_not_add_a_second_issue_either(
        self, voice_root, monkeypatch
    ):
        """The one-code-one-issue rule covers the OTHER producer too.

        A descriptor that raises already emitted this code independently, so a
        model with both faults handed React two `<li key="tts_model_incomplete">`
        entries printing the identical sentence. That predates the manifest;
        it is fixed here because the rule has to hold for every producer or it
        is not a rule."""
        from tts.adapters.xtts_v2 import XttsV2Adapter

        def boom(cls, model):
            raise RuntimeError("descriptor blew up")

        monkeypatch.setattr(XttsV2Adapter, "describe_settings",
                            classmethod(boom), raising=False)
        d = make_model(voice_root)
        (d / "vocab.json").unlink()                  # a REQUIRED file, gone

        r = verdict(d)
        assert r.settings_available is False, "ground: the descriptor did fail"
        details = incomplete_details(r)
        assert len(details) == 1, "one code, one issue, or React sees a dup key"
        assert "settings could not be read" in details[0]
        assert "vocab.json" in details[0]

    def test_two_models_in_one_root_do_not_borrow_each_other_s_manifest(
        self, voice_root
    ):
        """Every other test builds one folder, which is exactly the shape that
        would hide a leak between rows on a page that lists twenty."""
        good = make_model(voice_root, "aurora")
        record(good)
        broken = make_model(voice_root, "borealis")
        record(broken)
        (broken / "model.pth").write_bytes(b"")
        bare = make_model(voice_root, "cygnus")      # no manifest at all
        (bare / "model.pth").write_bytes(b"")

        models = {m.path: m for m in registry.scan_roots().models}
        verdicts = readiness.evaluate_all(list(models.values()))
        by_path = {models[p].uid: p for p in models}
        got = {by_path[uid]: codes(r) for uid, r in verdicts.items()}

        assert TTS_MODEL_INCOMPLETE not in got[str(good)]
        assert TTS_MODEL_INCOMPLETE in got[str(broken)]
        assert TTS_MODEL_INCOMPLETE not in got[str(bare)]

    def test_the_same_size_with_different_bytes_is_not_caught(self, voice_root):
        """The documented limit of choosing size over a hash, pinned so it is a
        known cost rather than a surprise. Swapping a file for another of the
        same length is invisible here, and catching it would mean reading
        gigabytes on every settings page render."""
        d = make_model(voice_root)
        record(d)
        (d / "model.pth").write_bytes(b"X" * len(WEIGHTS))   # same size
        assert TTS_MODEL_INCOMPLETE not in codes(verdict(d))

    def test_the_missing_file_path_still_works_with_a_manifest_present(
        self, voice_root
    ):
        """GROUND CONTROL for the pre-existing half of the check: a genuinely
        absent required file is still named the old way."""
        d = make_model(voice_root)
        record(d)
        (d / "vocab.json").unlink()
        details = incomplete_details(verdict(d))
        assert any(dt.startswith("missing from the model folder")
                   and "vocab.json" in dt for dt in details)

    def test_an_unreadable_manifest_cannot_take_the_verdict_down(
        self, voice_root, monkeypatch
    ):
        """A readiness verdict must not fail because a model folder is odd -
        the same promise `_already_on_the_card` makes.

        It patches `_entries`, which is the function `short_files` actually
        calls, on a folder that HAS a manifest. Patching `read` instead was
        the first attempt and it was decoration: readiness never calls `read`,
        so the explosion never happened and the test proved nothing."""
        d = make_model(voice_root)
        record(d)
        fired = []

        def boom(_path):
            fired.append(1)
            raise RuntimeError("the disk is having a day")

        monkeypatch.setattr(manifest, "_entries", boom)
        r = verdict(d)                                   # must not raise
        assert fired, "the explosion never happened; this proves nothing"
        assert r.runnable is True

    def test_a_batch_verdict_agrees_with_the_single_one(self, voice_root):
        """evaluate_all is the path the settings page actually takes; a check
        that only fires on the single-model route is a check nobody sees."""
        d = make_model(voice_root)
        record(d)
        (d / "model.pth").write_bytes(b"")
        model = next(m for m in registry.scan_roots().models
                     if m.path == str(d))
        batch = readiness.evaluate_all([model])[model.uid]
        assert codes(batch) == codes(readiness.evaluate(model))
        assert TTS_MODEL_INCOMPLETE in codes(batch)

    def test_the_manifest_does_not_stop_the_folder_being_recognised(
        self, voice_root
    ):
        """It lands beside the weights, so the scan has to walk straight past
        it. A model that stops being a model once it has a manifest would be a
        spectacular own goal."""
        d = make_model(voice_root)
        record(d)
        scan = registry.scan_roots()
        assert [m.path for m in scan.models] == [str(d)]
        assert scan.unrecognized == []


class TestItReachesTheWire:
    """A verdict the UI never receives is a verdict nobody sees.

    `GET /tts/models/{uid}/readiness` (routers/tts.py:179) is what the settings
    page calls, and it serialises through `Readiness.to_json`. Everything above
    this class works on the Python object, so without these two the check could
    be perfect and still stop at the process boundary.
    """

    def test_the_endpoint_reports_a_truncated_file_as_a_blocker(
        self, voice_root, client
    ):
        d = make_model(voice_root)
        record(d)
        (d / "model.pth").write_bytes(b"")

        listing = client.get("/api/v1/tts/models").json()
        uid = next(m["uid"] for m in listing["models"] if m["path"] == str(d))
        body = client.get(f"/api/v1/tts/models/{uid}/readiness").json()

        assert body["runnable"] is False
        blockers = [i for i in body["issues"] if i["severity"] == BLOCKER]
        assert [i["code"] for i in blockers].count(TTS_MODEL_INCOMPLETE) == 1
        issue = next(i for i in blockers if i["code"] == TTS_MODEL_INCOMPLETE)
        assert issue["action"] == ACTION_REDOWNLOAD
        assert "model.pth" in issue["detail"]

    def test_the_endpoint_says_nothing_about_a_healthy_one(
        self, voice_root, client
    ):
        """POSITIVE CONTROL: the same request on the same folder, intact."""
        d = make_model(voice_root)
        record(d)
        listing = client.get("/api/v1/tts/models").json()
        uid = next(m["uid"] for m in listing["models"] if m["path"] == str(d))
        body = client.get(f"/api/v1/tts/models/{uid}/readiness").json()
        assert body["runnable"] is True
        assert TTS_MODEL_INCOMPLETE not in [i["code"] for i in body["issues"]]


# ── the guard rail on this test file itself ─────────────────────────────────

def test_the_synthetic_folder_is_the_real_thing_as_far_as_the_scan_cares(
    voice_root,
):
    """If `make_model` stopped producing something the registry recognises,
    every assertion above would still pass by never finding a model at all.
    This is the one test that would notice."""
    d = make_model(voice_root)
    result = registry.identify_dir(d)
    assert result is not None
    assert result.engine_id == "xtts_v2"
    assert result.missing == ()


def test_the_manifest_name_collides_with_nothing_an_engine_owns(tmp_path):
    """It is written into a folder full of somebody else's files. Taking a
    name an engine already uses would overwrite part of the model with a
    directory listing."""
    owned = {name
             for adapter in registry.all_adapters()
             for name, _size in adapter.signature_files(tmp_path)}
    assert owned, "no adapter named any file, so this proves nothing"
    assert MANIFEST_NAME not in owned
    assert MANIFEST_NAME != registry.SIDECAR_NAME
