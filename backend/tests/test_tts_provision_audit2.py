"""Audit-2 - provision's three destruction paths, closed.

The worst chain the audit demonstrated: a locked file in the old environment
made rmtree half-gut it, os.replace then raised, and the error path deleted
the fully VERIFIED staging - ending with no usable environment at all while
runtimes.json still said "ready". The rename-aside swap must make that chain
impossible: a locked old environment fails CLEANLY with both environments
intact and a coded error.
"""
import sys
import time
from pathlib import Path

import pytest

import config
from tts import provision, runtimes
from tts.errors import TTS_RUNTIME_INSTALLING, TTS_RUNTIME_INSTALL_FAILED


@pytest.fixture
def voice_dir(monkeypatch, tmp_path):
    root = tmp_path / "voice"
    (root / "envs").mkdir(parents=True)
    (root / "bin").mkdir(parents=True)
    monkeypatch.setattr(config, "TTS_DIR", root, raising=False)
    monkeypatch.setattr(config, "TTS_ENVS_DIR", str(root / "envs"), raising=False)
    monkeypatch.setattr(config, "TTS_BIN_DIR", str(root / "bin"), raising=False)
    monkeypatch.setattr(config, "TTS_PY_DIR", str(root / "python"), raising=False)
    monkeypatch.setattr(config, "TTS_UV_CACHE_DIR", str(root / "uv-cache"), raising=False)
    monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(root / "runtimes.json"),
                        raising=False)
    provision.reset_jobs()
    yield root
    provision.reset_jobs()


def _fake_uv(monkeypatch, tmp_path):
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"")
    monkeypatch.setattr(provision, "find_uv", lambda: str(uv))
    return uv


class _OkInstaller:
    """Builds a working staging env, quickly."""

    def __call__(self, argv, *, on_line, cancel, timeout, env=None):
        if "venv" in argv:
            target = Path(argv[argv.index("venv") + 1])
            (target / "Scripts").mkdir(parents=True, exist_ok=True)
            (target / "Scripts" / "python.exe").write_bytes(b"new")
        on_line("ok")
        return 0, ""


def _await(engine_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = provision.job(engine_id)
        if job["state"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("never finished: %r" % provision.job(engine_id))


class TestRenameAsideSwap:
    def test_a_locked_old_environment_fails_clean_with_both_envs_intact(
        self, voice_dir, monkeypatch, tmp_path
    ):
        """The audit's exact chain. With the old env's python.exe held open,
        the swap must refuse - old env untouched, VERIFIED staging preserved,
        runtime registration still pointing at the working install."""
        if sys.platform != "win32":
            pytest.skip("directory-lock semantics are the Windows case")
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _OkInstaller())
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)

        # A previously working environment, registered as ready.
        old_env = voice_dir / "envs" / "fish_s2" / "Scripts"
        old_env.mkdir(parents=True)
        old_py = old_env / "python.exe"
        old_py.write_bytes(b"old interpreter")
        runtimes.register("fish_s2", str(old_py))

        with open(old_py, "rb"):                 # the lock (a running worker)
            provision.start_install("fish_s2")
            job = _await("fish_s2")

        assert job["state"] == "failed"
        assert job["error_code"] == TTS_RUNTIME_INSTALL_FAILED
        # The old environment survived, byte for byte.
        assert old_py.read_bytes() == b"old interpreter"
        # And the registration still points at a real interpreter.
        assert runtimes.status("fish_s2").state == "ready"

    def test_an_unlocked_swap_replaces_and_cleans_the_old_copy(
        self, voice_dir, monkeypatch, tmp_path
    ):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _OkInstaller())
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)

        old_env = voice_dir / "envs" / "fish_s2" / "Scripts"
        old_env.mkdir(parents=True)
        (old_env / "python.exe").write_bytes(b"old interpreter")

        provision.start_install("fish_s2")
        job = _await("fish_s2")
        assert job["state"] == "done"
        assert (voice_dir / "envs" / "fish_s2" / "Scripts" / "python.exe"
                ).read_bytes() == b"new"
        assert not (voice_dir / "envs" / "fish_s2.old").exists()
        assert runtimes.status("fish_s2").state == "ready"


class TestUninstallSlot:
    def test_uninstall_occupies_the_job_slot_so_an_install_cannot_start_under_it(
        self, voice_dir, monkeypatch, tmp_path
    ):
        """C8: uninstall used to re-check once and then pop whatever job held
        the slot after a multi-GB rmtree - including a brand-new install's."""
        _fake_uv(monkeypatch, tmp_path)
        started = {"install": False}

        real_rmtree = provision.shutil.rmtree

        def slow_rmtree(path, **kw):
            # Probe exactly once, on the FIRST removal (the env itself): the
            # uninstall also sweeps the uv cache AFTER releasing its slot, and
            # that later rmtree is legitimately not busy any more.
            if not started["install"]:
                started["install"] = True
                with pytest.raises(provision.ProvisionError) as exc:
                    provision.start_install("fish_s2")
                assert exc.value.code == TTS_RUNTIME_INSTALLING
            return real_rmtree(path, **kw)

        env = voice_dir / "envs" / "fish_s2"
        env.mkdir(parents=True)
        monkeypatch.setattr(provision.shutil, "rmtree", slow_rmtree)
        provision.uninstall("fish_s2")
        assert started["install"], "the concurrent install was never attempted"
        # And the slot is free again afterwards.
        assert provision.job("fish_s2")["running"] is False


class TestAtomicUvExtract:
    def test_a_mid_extract_failure_leaves_no_uv_at_the_final_path(
        self, voice_dir, monkeypatch
    ):
        """C9: a truncated uv.exe at the final path would be trusted by
        find_uv forever - voice setup permanently bricked."""
        import io
        import zipfile as zf_mod

        bin_dir = Path(config.TTS_BIN_DIR)
        bin_dir.mkdir(parents=True, exist_ok=True)
        target = bin_dir / ("uv.exe" if sys.platform == "win32" else "uv")

        # Build a real zip containing uv.exe, then make the copy explode.
        buf = io.BytesIO()
        with zf_mod.ZipFile(buf, "w") as z:
            z.writestr("uv-x86_64/uv.exe", b"binary" * 100)
        archive_bytes = buf.getvalue()

        import hashlib
        digest = hashlib.sha256(archive_bytes).hexdigest()
        monkeypatch.setattr(provision, "UV_SHA256", digest, raising=False)

        def fake_urlopen(url, timeout=0):
            return io.BytesIO(archive_bytes)

        monkeypatch.setattr(provision, "_urlopen", fake_urlopen, raising=False)

        def exploding_copy(src, dst, *a, **k):
            dst.write(b"trunc")
            raise OSError("disk full")

        monkeypatch.setattr(provision.shutil, "copyfileobj", exploding_copy)
        got = provision._download_uv(lambda ln: None)
        assert got is None
        assert not target.exists(), "a truncated uv.exe was left to be trusted"
