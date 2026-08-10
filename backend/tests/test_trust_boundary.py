"""Audit KÖK 11: the packaged product's trust boundary.

Everything here is the irreversible class. Once a plaintext image has been
written to %TEMP% it cannot be unwritten; once a page on 127.0.0.1:5173 has
read the user's chats it cannot un-read them. The fixes are small and
independent; what they share is that failure is permanent, which is why they
sit near the top of the work list rather than near the interesting parts.
"""

from __future__ import annotations

import importlib
import io
import os
import sys
from pathlib import Path

import pytest

from PIL import Image

import config

#: Absolute, because a test that only passes from one directory is a test that
#: will surprise somebody. Running `pytest backend/` from the repo root used to
#: fail eleven tests across four files with FileNotFoundError on a relative
#: path like 'tts/provision.py'. Measured 2026-08-10 and fixed here.
BACKEND = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. the dev origin must not ship
# ---------------------------------------------------------------------------

def test_the_dev_origin_is_trusted_in_a_dev_tree():
    """The grant is real and load-bearing: 5173 -> 8787 is genuinely
    cross-origin, so removing it outright would break development."""
    assert config.FRONTEND_ORIGINS == ("http://127.0.0.1:5173",)


def test_the_dev_origin_is_not_trusted_in_a_frozen_build(monkeypatch):
    """The packaged exe serves its own SPA same-origin and never needs 5173,
    but the constant was unconditional - so any local page on that port could
    read /chats, /messages, /characters and /personas while the vault was
    open, and POST /settings/proxy to redirect traffic to a host of its
    choosing."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    frozen = importlib.reload(config)
    try:
        assert frozen.FRONTEND_ORIGINS == ()
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_a_page_on_5173_cannot_write_to_a_frozen_build(client, monkeypatch):
    """The end that matters: the CSRF shield's allow-set must not contain the
    dev origin once frozen."""
    import main
    monkeypatch.setattr(main, "FRONTEND_ORIGINS", ())
    r = client.post(
        "/api/v1/settings/proxy",
        json={"proxy_url": "http://evil.invalid:8080", "proxy_required": True},
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "cross_origin_denied"


def test_the_dev_origin_still_works_unfrozen(client):
    r = client.get("/api/v1/settings",
                   headers={"Origin": "http://127.0.0.1:5173"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 2. the ~1 MiB band that wrote plaintext images to disk
# ---------------------------------------------------------------------------

def test_the_spool_ceiling_sits_above_the_body_shield():
    """The whole bug in one assertion.

    The shield admitted bodies up to MAX_UPLOAD_BYTES + 1 MiB while the spool
    rolled to disk above MAX_UPLOAD_BYTES + 1, so every request in between was
    written to %TEMP% in the clear and THEN rejected with 400 - leaving the
    user believing nothing had been stored. Ordering these two numbers is what
    makes that band empty.
    """
    assert config.UPLOAD_SPOOL_LIMIT > config.UPLOAD_BODY_LIMIT
    assert config.UPLOAD_BODY_LIMIT > config.MAX_UPLOAD_BYTES


def test_all_three_limits_come_from_one_constant():
    """They were three independently-written numbers; a band opened because
    two of them were edited and the third was not."""
    assert config.UPLOAD_BODY_LIMIT == (
        config.MAX_UPLOAD_BYTES + config.UPLOAD_MULTIPART_OVERHEAD
    )
    assert config.UPLOAD_SPOOL_LIMIT == config.UPLOAD_BODY_LIMIT + 1

    import main
    from routers import uploads
    from starlette.formparsers import MultiPartParser
    assert main._UPLOAD_BODY_LIMIT == config.UPLOAD_BODY_LIMIT
    assert MultiPartParser.spool_max_size == config.UPLOAD_SPOOL_LIMIT


def test_an_upload_in_the_old_band_never_reaches_a_temp_file(client, monkeypatch):
    """Behavioural, not arithmetic: send a body inside the former band and
    assert Starlette never rolled a file part to disk."""
    from starlette.formparsers import MultiPartParser

    rolled: list[int] = []
    real_rollover = io.BytesIO  # placeholder to keep the name obvious

    import tempfile
    real_spooled = tempfile.SpooledTemporaryFile.rollover

    def _spy(self, *a, **kw):
        rolled.append(1)
        return real_spooled(self, *a, **kw)

    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", _spy)

    # Just inside the shield, well past the OLD spool ceiling.
    size = config.MAX_UPLOAD_BYTES + 4096
    r = client.post(
        "/api/v1/uploads/images",
        files={"file": ("big.png", b"\x00" * size, "image/png")},
    )
    # It must be refused - but by the handler, in RAM.
    assert r.status_code == 400
    assert not rolled, "the image was spooled to a plaintext temp file"


def test_a_chunked_upload_is_refused_rather_than_read(client):
    """The shield keyed off Content-Length and simply did not run without it,
    so a chunked POST skipped the check its own comment promises."""
    r = client.post(
        "/api/v1/uploads/images",
        content=iter([b"\x00" * 1024]),
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    assert r.status_code == 411


# ---------------------------------------------------------------------------
# 3. character import buffered the whole body before checking the cap
# ---------------------------------------------------------------------------

def test_a_declared_oversize_import_is_refused_before_the_body_is_read(client):
    from routers.characters import MAX_IMPORT_BYTES

    r = client.post(
        "/api/v1/characters/import",
        content=b"{}",
        headers={"Content-Length": str(MAX_IMPORT_BYTES + 1),
                 "Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "character_json_too_large"


def test_an_undeclared_oversize_import_is_cut_off_mid_stream(client):
    """A sender that lies about the length, or declares none, still must not
    get the whole body into RAM first."""
    from routers.characters import MAX_IMPORT_BYTES

    sent = 0

    def _body():
        nonlocal sent
        for _ in range(64):
            chunk = b"x" * (MAX_IMPORT_BYTES // 8)
            sent += len(chunk)
            yield chunk

    r = client.post(
        "/api/v1/characters/import",
        content=_body(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert sent < MAX_IMPORT_BYTES * 64, "the whole body was buffered anyway"


def test_a_legal_import_still_works(client):
    r = client.post("/api/v1/characters/import",
                    json={"name": "Aoife", "description": "a test character"})
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# 4. decompression-bomb check must not depend on a process-global filter
# ---------------------------------------------------------------------------

def test_the_bomb_check_does_not_touch_the_global_warning_filters(client, monkeypatch):
    """warnings.catch_warnings swaps a PROCESS-GLOBAL list and is documented
    as not thread-safe; save_upload runs in the anyio threadpool, so one
    upload's context-manager exit could restore the filters while another was
    still decoding - letting a bomb through the check that exists to stop it.

    Behavioural, not textual: monkeypatch warnings.catch_warnings and
    warnings.simplefilter to recorders, run the REAL save_upload on a valid
    image, and assert neither was called. `client` is here for its unlocked
    vault, same as the control below - save_upload writes a blob.

    Honesty about what this proves: it is a seam test, not an exhaustive one.
    It catches the exact incident this guards against (a context manager
    around the pixel check) and any direct call to these two names by any
    spelling. It would NOT catch code that reached the same process-global
    filter list through some other door - ctypes, a private CPython
    attribute, or a reference to the original functions captured before this
    monkeypatch ran. That gap is real but far narrower than the string-split
    it replaces, which any comment, rename, or reformat would have defeated
    outright.
    """
    import warnings
    import attachments_service

    calls: list[str] = []

    class _RecordingContext:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def _record_catch_warnings(*args, **kwargs):
        calls.append("catch_warnings")
        return _RecordingContext()

    def _record_simplefilter(*args, **kwargs):
        calls.append("simplefilter")

    monkeypatch.setattr(warnings, "catch_warnings", _record_catch_warnings)
    monkeypatch.setattr(warnings, "simplefilter", _record_simplefilter)

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format="PNG")
    result = attachments_service.save_upload(buf.getvalue(), "image/png")

    assert result["id"]
    assert calls == [], f"the global filter mechanism was touched: {calls}"


def test_an_oversized_image_is_rejected_from_its_header_alone(monkeypatch):
    """Deterministic and pixel-free: Image.open parses only the header, so the
    dimensions are known before a single pixel is decoded."""
    import attachments_service

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 64)
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format="PNG")

    with pytest.raises(attachments_service.AttachmentError) as exc:
        attachments_service.save_upload(buf.getvalue(), "image/png")
    assert "attachment_invalid" in str(exc.value)


def test_a_normal_image_still_decodes(client):
    """The control for the test above: the header check must reject bombs
    without rejecting ordinary uploads. `client` is here for its unlocked
    vault - save_upload writes a blob."""
    import attachments_service

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format="PNG")
    assert attachments_service.save_upload(buf.getvalue(), "image/png")


# ---------------------------------------------------------------------------
# 5. never execute something found in the working directory
# ---------------------------------------------------------------------------

def test_an_exe_in_the_working_directory_is_not_trusted(tmp_path, monkeypatch):
    """shutil.which searches os.curdir FIRST on Windows. An app launched by
    double-clicking it in Downloads has that folder as its cwd, so a uv.exe
    sitting among the user's other downloads would be run with the
    application's full environment - skipping the SHA-256 pin entirely."""
    from tts._which import which_trusted

    planted = tmp_path / "uv.exe"
    planted.write_bytes(b"MZ hostile")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: str(planted))

    assert which_trusted("uv.exe") is None


def test_an_exe_elsewhere_on_path_is_still_returned(tmp_path, monkeypatch):
    from tts._which import which_trusted

    elsewhere = tmp_path / "real"
    elsewhere.mkdir()
    real = elsewhere / "uv.exe"
    real.write_bytes(b"MZ real")
    cwd = tmp_path / "downloads"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("shutil.which", lambda name: str(real))

    assert which_trusted("uv.exe") == str(real)


def test_both_lookups_go_through_the_trusted_helper(tmp_path, monkeypatch):
    """provision.find_uv() and vram.query_gpu() both need an executable by
    name, and both used to reach shutil.which directly - which searches the
    working directory FIRST on Windows, the same hole tts/_which.py exists to
    close. A source grep for the literal `shutil.which(` proved neither call
    site still spelled it that way, but proved nothing about whether they
    actually ran through which_trusted: `getattr(shutil, "which")`, an
    `import shutil as sh`, or any other alias would pass the grep and still
    execute whatever sits in the cwd.

    Behavioural instead: stub shutil.which itself - exactly like the sibling
    test test_an_exe_in_the_working_directory_is_not_trusted - to report a
    file planted in tmp_path as found, chdir into tmp_path, and call the two
    REAL entry points. If a call site goes through which_trusted, the cwd
    filter inside it rejects that result and the hostile path is never
    chosen (find_uv) or never reaches subprocess.run's argv (query_gpu). If a
    call site regressed to a direct shutil.which(), the filter is skipped and
    the hostile path comes straight through - which is exactly what would
    turn each assertion below red.
    """
    import subprocess

    from tts import provision, vram

    hostile = tmp_path / "uv.exe"
    hostile.write_bytes(b"MZ hostile")
    monkeypatch.chdir(tmp_path)
    # Simulates shutil.which's documented cwd-first behaviour directly rather
    # than depending on the real Windows API, which the underlying which_trusted
    # test already establishes as the right seam for this class of test.
    monkeypatch.setattr("shutil.which", lambda name: str(hostile))

    # Starve find_uv()'s other two legitimate sources so only the trust
    # check decides what comes back.
    own_bin = tmp_path / "own_bin"
    own_bin.mkdir()
    monkeypatch.setattr(config, "TTS_BIN_DIR", str(own_bin))
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    found_uv = provision.find_uv()
    assert found_uv != str(hostile), (
        "find_uv() returned the executable shutil.which reported from the "
        "working directory - it is not routing through which_trusted"
    )

    seen_argv: list[list[str]] = []

    def _spy_run(argv, *args, **kwargs):
        seen_argv.append(list(argv))

        class _Result:
            returncode = 1
            stdout = ""

        return _Result()

    monkeypatch.setattr(subprocess, "run", _spy_run)
    gpu = vram.query_gpu()
    assert gpu is None
    for argv in seen_argv:
        assert argv[0] != str(hostile), (
            f"nvidia-smi was invoked with the cwd-shadowed path: {argv}"
        )


# ---------------------------------------------------------------------------
# 6. two concurrent installs must not share one download path
# ---------------------------------------------------------------------------

def test_the_uv_download_uses_a_per_call_temp_name(tmp_path, monkeypatch):
    """_JOBS is keyed per engine, so two engines set up back to back run two
    downloads at once - and both wrote bin/uv.zip.partial. They interleaved,
    the SHA-256 pin correctly refused the result, and both installs failed
    with TTS_PYTHON_NOT_FOUND, blaming the machine.

    The old test grepped for the absent old literal and for the presence of
    os.getpid() / threading.get_ident() ANYWHERE in a 900-line file - true
    even if the call that matters never uses them, and broken by a harmless
    quote-style change.

    Behavioural instead: drive the REAL _download_uv helper from two threads
    at once, with the network read stubbed to block on a barrier until both
    threads have started their write - forcing them to genuinely overlap,
    not just run one after another - and spy on the builtin `open` to record
    the two paths actually opened for writing. The two threads guarantee two
    different thread idents, which is what the fix's naming actually keys
    on: a same-thread sequential rerun of this helper would still collide,
    since the name is a pure function of (pid, thread ident) with no
    counter, but that scenario cannot arise from how the app calls it - one
    thread per install job.
    """
    import builtins
    import threading

    from tts import provision

    monkeypatch.setattr(config, "TTS_BIN_DIR", str(tmp_path / "bin"))

    barrier = threading.Barrier(2, timeout=5)

    class _FakeResponse:
        def __init__(self):
            self._sent = False

        def read(self, n):
            if not self._sent:
                self._sent = True
                # Do not release either thread's first chunk until BOTH
                # threads have reached this point - guaranteeing their
                # archive files are open at the same time, not merely in
                # sequence.
                barrier.wait()
                return b"not a real uv archive - just enough to hash"
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    class _FakeOpener:
        def open(self, url, timeout=None):
            return _FakeResponse()

    monkeypatch.setattr(provision, "_url_opener", lambda: _FakeOpener())

    opened: list[str] = []
    opened_lock = threading.Lock()
    real_open = builtins.open

    def _spy_open(file, mode="r", *args, **kwargs):
        text = str(file)
        if "uv.zip.partial" in text:
            with opened_lock:
                opened.append(text)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _spy_open)

    errors: list[BaseException] = []

    def _run_one():
        try:
            provision._download_uv(lambda line: None, cancel=None)
        except BaseException as exc:                      # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=_run_one)
    t2 = threading.Thread(target=_run_one)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, f"the download helper raised: {errors}"
    assert len(opened) == 2, f"expected two staged downloads, saw {opened}"
    assert opened[0] != opened[1], (
        "two concurrent downloads used the SAME partial path - they would "
        "interleave into one corrupt file, exactly as in the incident this "
        "test guards against"
    )
