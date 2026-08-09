"""tts/provision.py - the app builds the voice environment. Nobody else does.

This is an acceptance criterion, not an optimisation: the user presses one
thing in Settings and voice becomes available. They never install Python, never
edit runtimes.json, never see a command line. That the engines need their own
interpreters - with dependency sets that contradict each other and the app - is
an implementation detail they should never have to learn.

WHY uv AND NOT venv
    A frozen onefile app cannot be its own interpreter. `sys.executable` is the
    bootloader, and PyInstaller starts CPython with argv parsing disabled, so
    `[sys.executable, "-m", "venv", ...]` does not fail - it silently relaunches
    the whole GUI with "-m venv" in argv. Inside a retry loop that is a fork
    bomb. uv is a single static binary that can fetch its own CPython, so there
    is no dependency on what the user happens to have installed.

WHAT IS PINNED AND WHY
    requirements/<engine>.txt is a full freeze of the environment where the
    engine was actually measured working. Nothing here resolves "latest".

THE RULE THAT MAKES IT SAFE
    An environment is registered as ready ONLY after its imports are proven to
    work. A successful install command is not evidence: a resolver can serve a
    CPU-only torch, a wheel can land half-written. Telling someone voice is
    ready and then handing them silence is the worst outcome available, so the
    last step is to make the new interpreter prove itself.

NETWORK
    This is the one part of Elysium that talks to anywhere but OpenRouter, and
    only when the user explicitly asks for the install. It downloads packages;
    it uploads nothing, and no chat, persona or voice data is in scope.
"""
from __future__ import annotations

import hashlib
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import config
import launch_token

from . import runtimes
from ._which import which_trusted
from .errors import (
    TTS_ENGINE_UNKNOWN,
    TTS_INSUFFICIENT_DISK,
    TTS_PYTHON_NOT_FOUND,
    TTS_RUNTIME_INSTALLING,
    TTS_RUNTIME_INSTALL_FAILED,
)

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000
LOG_KEEP_LINES = 400

PYTHON_VERSION = "3.12"

# What each engine must be able to import before we call it ready, and roughly
# how much it will pull down (shown before the user commits to the wait).
# The verify lists cover what the WORKER SCRIPTS actually import at load time,
# not just the engine package - torchaudio in particular is its own wheel, and
# an environment without it would pass a narrower check and then die on load.
ENGINES: dict[str, dict] = {
    "fish_s2": {
        "verify": ["torch", "torchaudio", "torchao", "numpy", "soundfile",
                   "fish_speech"],
        "download_mb": 4200,
        "torch_backend": "cu128",
    },
    "xtts_v2": {
        "verify": ["torch", "torchaudio", "numpy", "TTS"],
        "download_mb": 3400,
        "torch_backend": "cu128",
    },
    "chatterbox": {
        "verify": ["torch", "torchaudio", "numpy", "librosa", "perth",
                   "chatterbox"],
        "download_mb": 3600,
        "torch_backend": "cu128",
    },
}

# The pinned uv used to build environments when the machine has none.
# 0.11.7 is the version every measured install in this project ran through
# (incl. --torch-backend=cu128); the hash pins the exact artefact.
UV_VERSION = "0.11.7"
UV_URL = ("https://github.com/astral-sh/uv/releases/download/"
          f"{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip")
# Computed from the release artefact itself (23,572,531 bytes, 2026-07-24).
UV_SHA256 = "fe0c7815acf4fc45f8a5eff58ed3cf7ae2e15c3cf1dceadbd10c816ec1690cc1"


class ProvisionError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass
class InstallPlan:
    engine_id: str
    env_dir: str
    requirements: str
    python_version: str
    download_mb: int

    def to_json(self) -> dict:
        return {
            "engine_id": self.engine_id,
            "env_dir": self.env_dir,
            "requirements": self.requirements,
            "python_version": self.python_version,
            "download_mb": self.download_mb,
        }


@dataclass
class _Job:
    engine_id: str
    state: str = "idle"          # idle|preparing|installing|verifying|done|failed|cancelled
    log: list = field(default_factory=list)
    error_code: str | None = None
    error_detail: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event)

    def to_json(self) -> dict:
        return {
            "engine_id": self.engine_id,
            "state": self.state,
            "log": list(self.log[-40:]),
            "error_code": self.error_code,
            "error_detail": self.error_detail,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "running": self.state in ("preparing", "installing", "verifying", "uninstalling"),
        }


_JOBS: dict[str, _Job] = {}
_JOBS_LOCK = threading.Lock()

# App exit must stop installers too: their uv subprocesses are not daemon
# threads and would keep downloading after the window closed.
from .worker_client import register_teardown as _register_teardown  # noqa: E402

_register_teardown(lambda grace: cancel_all())


def reset_jobs() -> None:
    """Test seam. Never called by the app."""
    with _JOBS_LOCK:
        _JOBS.clear()


# ── paths ────────────────────────────────────────────────────────────────────

def requirements_path(engine_id: str) -> Path:
    return Path(__file__).resolve().parent / "requirements" / f"{engine_id}.txt"


def env_dir(engine_id: str) -> Path:
    return Path(config.TTS_ENVS_DIR) / engine_id


def env_python(engine_id: str) -> Path:
    d = env_dir(engine_id)
    return d / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def plan(engine_id: str) -> InstallPlan:
    spec = ENGINES.get(engine_id)
    if spec is None:
        raise ProvisionError(TTS_ENGINE_UNKNOWN, f"unknown engine {engine_id}")
    req = requirements_path(engine_id)
    if not req.is_file():
        raise ProvisionError(TTS_RUNTIME_INSTALL_FAILED,
                             "the pinned requirements file is missing")
    return InstallPlan(engine_id, str(env_dir(engine_id)), str(req),
                       PYTHON_VERSION, int(spec["download_mb"]))


def plan_payload(engine_id: str) -> dict:
    """The plan plus whether a GPU is even visible - shown BEFORE the user
    commits to a multi-GB download, because every engine here needs CUDA and
    finding that out after 3 GB is the wrong moment."""
    from .vram import query_gpu

    body = plan(engine_id).to_json()
    body["gpu_available"] = query_gpu() is not None
    return body


# ── uv ───────────────────────────────────────────────────────────────────────

def find_uv() -> str | None:
    """Our own copy first, then whatever the machine already has.

    Never `sys._MEIPASS`: in a onefile build that is a fresh temp directory
    extracted per launch and deleted on exit, so anything installed from there
    would point at a path that stops existing.
    """
    name = "uv.exe" if IS_WINDOWS else "uv"
    own = Path(config.TTS_BIN_DIR) / name
    if own.is_file():
        return str(own)
    # which_trusted, not shutil.which: the latter searches the working
    # directory first on Windows, so a uv.exe sitting in the folder the app
    # was launched from would be run with our full environment - skipping the
    # SHA-256 pin this module enforces on its OWN copy two lines above.
    found = which_trusted(name)
    if found:
        return found
    home = Path.home() / ".local" / "bin" / name
    return str(home) if home.is_file() else None


class ProxyUnreadable(Exception):
    """The vault could not answer whether a proxy is configured."""


def _read_proxy() -> str | None:
    """The user's configured proxy, or None - raising when it cannot be read.

    Provisioning makes MULTI-GIGABYTE outbound connections - GitHub for the uv
    binary, then PyPI and download.pytorch.org through uv - and none of them
    read the vault-stored proxy. A user who configured a proxy (even with
    proxy_required ON, which blocks completions outright when it is unhealthy)
    had their real IP contact three third-party hosts the moment they pressed
    "Set up voice", with nothing anywhere saying the proxy was not used.

    The distinction this raise exists to keep: "no proxy is configured" and
    "the vault is locked so nobody knows" are different answers, and collapsing
    them into None is how the second one silently became the first.
    """
    try:
        from config import SECRET_PROXY_URL
        from secrets_service import get_secret

        return (get_secret(SECRET_PROXY_URL) or "").strip() or None
    except Exception as exc:                             # noqa: BLE001
        raise ProxyUnreadable(str(exc)[:200]) from exc


def _proxy_url() -> str | None:
    """_read_proxy for the callers that must not raise (env, opener)."""
    try:
        return _read_proxy()
    except ProxyUnreadable:
        return None


def _proxy_required() -> bool:
    """Whether the user made the proxy mandatory. FAILS CLOSED.

    This used to answer False on any exception, which is the wrong direction
    for a switch whose whole purpose is "do not go out without the proxy": an
    unreadable setting became permission to download 2.6 GB direct, from the
    one code path where the user is least able to see it happen. Not knowing
    has to mean "assume it is required" - the cost is a refusal with a clear
    message, and the cost of the other answer is the leak the setting exists
    to prevent.
    """
    try:
        from database import get_setting

        return (get_setting("proxy_required") or "") == "1"
    except Exception:                                    # noqa: BLE001
        logger.warning("provision: cannot read proxy_required - "
                       "treating the proxy as mandatory")
        return True


#: Ambient variables that redirect where a child downloads from, or let it
#: skip the proxy. uv and pip read all of these, and _run hands the child a
#: copy of os.environ, so every one of them was in force.
_ENV_NETWORK_STRIP = (
    # A wildcard here makes uv ignore the proxy for every host - the exact
    # bypass the proxy exists to prevent, spelled in one variable.
    "NO_PROXY", "no_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
    # Where the packages come from. Redirecting these points a multi-gigabyte
    # install at somebody else's host: a privacy leak and a supply-chain one
    # in the same variable.
    "UV_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX",
    "UV_FIND_LINKS", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
    # The PIP_* half is defensive rather than known-necessary: the installer
    # only ever runs `uv pip install`, and uv does not read pip's own config
    # the way pip does. Stripping them anyway means a future switch back to
    # pip cannot quietly reintroduce the redirect.
    "PIP_TRUSTED_HOST", "PIP_FIND_LINKS", "PIP_CONFIG_FILE",
)


def ambient_proxy_names() -> list[str]:
    """Proxy variables the SHELL has set, which the child will not inherit.

    Stripping them is right - everything else in this app builds its clients
    with trust_env=False, and an exported proxy is a host the user never chose
    here. But there is a real population it costs: someone on a corporate
    network whose machine routes everything through a system proxy and who has
    therefore never opened Elysium's proxy setting. Their install used to work
    by inheritance and now cannot reach anything.

    Refusing to trust the shell and refusing to SAY SO are separate decisions.
    This is what lets the failure read as "your proxy was not used, here is
    where to set it" instead of a timeout that looks like a broken network.
    """
    return [name for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                              "http_proxy", "https_proxy", "all_proxy")
            if os.environ.get(name)]


def _proxy_env(base: dict) -> dict:
    """`base`, with the ambient network environment removed and ours applied.

    Stripping happens whether or not a proxy is configured. Everywhere else in
    this app builds its clients with trust_env=False; the installer was the one
    place that inherited the user's shell, so a machine with HTTP_PROXY
    exported sent the whole download through a host this app never chose, and
    a machine with NO_PROXY=* sent it through none.
    """
    env = {**base, **{name: None for name in _ENV_NETWORK_STRIP}}
    proxy = _proxy_url()
    if proxy:
        env.update({
            "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy,
            "http_proxy": proxy, "https_proxy": proxy, "all_proxy": proxy,
        })
    return env


def _url_opener():
    """A urllib opener that honours the CONFIGURED proxy.

    The default opener consults the process environment; ours must consult the
    vault, which is the only place this app stores a proxy. An empty
    ProxyHandler is passed when none is set - deliberately explicit, so ambient
    HTTP_PROXY variables the app does not trust (network_client builds every
    client with trust_env=False) cannot leak back in here.
    """
    import urllib.request

    proxy = _proxy_url()
    if not proxy and _proxy_required():
        # The gate in start_install runs before the job is queued; this one
        # runs at the moment of the connection. Between them the vault can
        # lock, and this is the ~25 MB request to GitHub that would otherwise
        # go out bare from a machine whose user made the proxy mandatory.
        raise ProxyUnreadable("a proxy is required but none is available")
    handler = urllib.request.ProxyHandler(
        {"http": proxy, "https": proxy} if proxy else {}
    )
    return urllib.request.build_opener(handler)


def _download_uv(on_line, cancel=None) -> str | None:
    """Fetch the pinned uv into our own bin directory - SHA-256 verified.

    This is what makes "Set up voice" one click on a machine that has nothing:
    without it, a user without uv would hit a dead end and the R1 criterion
    ("the user never opens a terminal") would be a lie. Only ever reached from
    an install the user explicitly started, and only ever DOWNLOADS - a zip
    whose hash does not match the pin is deleted, never run.
    """
    if not IS_WINDOWS:
        on_line("automatic uv download is only configured for Windows")
        return None
    if not UV_SHA256 or UV_SHA256.startswith("__"):
        # A build without the pin must refuse loudly, not fetch unverified.
        on_line("uv download pin is not configured in this build")
        return None
    import urllib.request

    bin_dir = Path(config.TTS_BIN_DIR)
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "uv.exe"
    # Per-call, not a fixed name. _JOBS is keyed per ENGINE, so on a machine
    # without uv, setting up two engines back to back runs two downloads at
    # once - and both used to write bin/uv.zip.partial. They interleaved into
    # one corrupt file, the SHA-256 pin then correctly refused it, and BOTH
    # installs died with TTS_PYTHON_NOT_FOUND ("no way to build an isolated
    # environment on this machine"), blaming the machine for a name collision.
    archive = bin_dir / f"uv.zip.partial.{os.getpid()}.{threading.get_ident()}"
    try:
        on_line(f"downloading uv {UV_VERSION} (about 25 MB)")
        # Through the CONFIGURED proxy (see _url_opener): the default opener
        # went direct, so a user with a proxy - even a mandatory one - had
        # their real IP contact github.com the moment they pressed Set up.
        with _url_opener().open(UV_URL, timeout=120) as resp, \
                open(archive, "wb") as out:
            digest = hashlib.sha256()
            while True:
                if cancel is not None and cancel.is_set():
                    # Cancel used to be a no-op here: the job was already in
                    # _JOBS with running=true so the UI drew its Cancel button,
                    # pressing it set this event and returned 200 - and this
                    # loop never looked, so the download ran to completion and
                    # the button appeared to have done nothing.
                    on_line("uv download cancelled")
                    return None
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest().lower() != UV_SHA256.lower():
            on_line("uv download did not match its pinned checksum - refused")
            return None
        with zipfile.ZipFile(archive) as zf:
            member = next((n for n in zf.namelist()
                           if n.rsplit("/", 1)[-1] == "uv.exe"), None)
            if member is None:
                on_line("the uv archive did not contain uv.exe")
                return None
            # Extract to a temp name and move into place only when complete:
            # a truncated uv.exe at the final path would be trusted by
            # find_uv() forever (audit-2 C9).
            # Per-call for the same reason as the archive above: a fixed name
            # here let two concurrent installs write one another's bytes into
            # the file that is then os.replace()d over uv.exe.
            part = target.with_suffix(
                f".exe.partial.{os.getpid()}.{threading.get_ident()}")
            try:
                with zf.open(member) as src, open(part, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(str(part), str(target))
            finally:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
        on_line("uv ready")
        return str(target)
    except Exception as exc:                    # noqa: BLE001
        on_line(f"could not fetch uv: {type(exc).__name__}")
        return None
    finally:
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            pass


def _free_gb(path: Path) -> float:
    try:
        target = path
        while not target.exists() and target.parent != target:
            target = target.parent
        return shutil.disk_usage(str(target)).free / (1024 ** 3)
    except Exception:                           # noqa: BLE001
        return float("inf")                     # do not block on a bad reading


# ── running commands ─────────────────────────────────────────────────────────

def _run(argv, *, on_line, cancel: threading.Event, timeout: float,
         env: dict | None = None) -> tuple[int, str]:
    """Run a command, streaming its output, killable at any moment.

    Output is drained continuously and decoded with errors="replace": a
    multi-gigabyte install writes far more than the 4096-byte pipe buffer, and
    a redirected child on this machine defaults to the ANSI code page, so a
    strict decode would raise inside the drain and hang the whole install.
    """
    full_env = dict(os.environ)
    # Same reason the engine worker strips it: uv runs setup code from wheels
    # this app did not write, and the launch token is the one credential that
    # would let such code ask the local API for the whole conversation.
    full_env.pop(launch_token.ENV_VAR, None)
    for name, value in (env or {}).items():
        # None means REMOVE. _proxy_env uses it to take the user's ambient
        # proxy and index variables away from the child; without this branch
        # they would survive as the string "None" and be worse than inherited.
        if value is None:
            full_env.pop(name, None)
        else:
            full_env[name] = value
    flags = CREATE_NO_WINDOW if IS_WINDOWS else 0
    try:
        proc = subprocess.Popen(
            list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=flags, env=full_env,
        )
    except OSError as exc:
        return 1, str(exc)[:300]

    # The loop must NOT be driven by the blocking stdout iterator: uv with a
    # redirected stdout is not a TTY, suppresses its progress bar, and can go
    # many minutes without a single line during the 2.6 GB torch fetch. Cancel
    # and the deadline have to fire on a WALL CLOCK, so a reader thread feeds a
    # queue and this loop polls it - a silent child stays killable.
    lines: queue.Queue = queue.Queue()

    def drain() -> None:
        try:
            for raw in proc.stdout:
                lines.put(raw.rstrip("\n"))
        except Exception:                       # noqa: BLE001
            pass
        finally:
            lines.put(None)                     # EOF marker

    threading.Thread(target=drain, name="tts-install-drain", daemon=True).start()

    tail: list[str] = []
    deadline = time.monotonic() + timeout
    eof = False
    while not eof:
        try:
            item = lines.get(timeout=0.25)
            if item is None:
                eof = True
            elif item:
                on_line(item)
                tail.append(item)
                del tail[:-12]
        except queue.Empty:
            pass
        if cancel.is_set():
            _kill_tree(proc)
            return 1, "cancelled"
        if time.monotonic() > deadline:
            _kill_tree(proc)
            return 1, "timed out"
    try:
        code = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        # stdout closed but the process lives on - a zombie is not a success.
        _kill_tree(proc)
        code = 1
    return code, " | ".join(tail[-4:])


def _kill_tree(proc: subprocess.Popen) -> None:
    """uv spawns children. terminate() on Windows is one process only, so the
    tree needs taskkill here - this is a short-lived installer, not the
    long-lived worker, so the job object is not worth its complexity."""
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        proc.kill()
    except Exception:                           # noqa: BLE001
        pass


def _verify_env(engine_id: str, python: Path, on_line, cancel) -> None:
    """Make the new interpreter prove itself before anyone depends on it.

    A successful install command is NOT evidence: the resolver can serve a
    CPU-only torch, a wheel can land half-written, a source package can fail to
    build and still leave the command green. This is the step that keeps
    "voice is ready" from being a lie.
    """
    spec = ENGINES.get(engine_id) or {}
    for module in spec.get("verify", []):
        on_line(f"checking {module}")
        code, detail = _run(
            [str(python), "-I", "-c", f"import {module}"],
            on_line=lambda ln: None, cancel=cancel, timeout=300,
        )
        if code != 0:
            if cancel.is_set():
                # The user pressed cancel; the import did not "fail", it was
                # interrupted. Reporting tts_runtime_install_failed here would
                # tell them their own cancel was a breakage.
                raise _Cancelled()
            raise ProvisionError(
                TTS_RUNTIME_INSTALL_FAILED,
                f"the new environment cannot import {module}: {detail}"[:300],
            )

    # CUDA visibility is reported, not required: a cu128 torch on a machine
    # whose driver is missing imports fine and simply cannot see the card.
    # Readiness reports that at load time; failing a 3 GB install for it would
    # punish someone whose driver update is a reboot away.
    code, _ = _run(
        [str(python), "-I", "-c",
         "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 7)"],
        on_line=lambda ln: None, cancel=cancel, timeout=300,
    )
    if code == 7:
        on_line("note: torch cannot see an NVIDIA GPU on this machine yet")


# ── the install ──────────────────────────────────────────────────────────────

def start_install(engine_id: str) -> dict:
    """Begin. Returns immediately; watch it with `job(engine_id)`."""
    p = plan(engine_id)

    # Reserve the slot ATOMICALLY, before the slow filesystem checks below.
    # Check-then-set with the lock released in between is a race: two clicks
    # could both pass the check and both start writing one environment - the
    # exact half-built-site-packages failure this guard exists to prevent.
    job = _Job(engine_id=engine_id, state="preparing", started_at=time.time())
    with _JOBS_LOCK:
        existing = _JOBS.get(engine_id)
        if existing is not None and existing.to_json()["running"]:
            raise ProvisionError(TTS_RUNTIME_INSTALLING, "already installing")
        _JOBS[engine_id] = job

    try:
        free = _free_gb(Path(config.TTS_ENVS_DIR))
        if free < float(config.TTS_INSTALL_MIN_FREE_GB):
            raise ProvisionError(
                TTS_INSUFFICIENT_DISK,
                f"{free:.1f} GB free, about {config.TTS_INSTALL_MIN_FREE_GB} GB needed",
            )

        # A proxy the user made MANDATORY must not be silently bypassed by a
        # multi-gigabyte download - the request where it matters most. Same
        # state completions and /models refuse outright.
        if _proxy_required():
            try:
                configured = _read_proxy()
            except ProxyUnreadable:
                # Not the same as "none configured", and it must not resolve
                # to "go ahead": the vault holds the proxy, so being unable to
                # read it is exactly when a download would go out bare.
                raise ProvisionError(
                    TTS_RUNTIME_INSTALL_FAILED,
                    "a proxy is required but the vault could not be read - "
                    "unlock it and try again",
                ) from None
            if not configured:
                raise ProvisionError(
                    TTS_RUNTIME_INSTALL_FAILED,
                    "a proxy is required but none is configured - set one in "
                    "Settings before installing a voice engine",
                )
        elif not _proxy_url() and ambient_proxy_names():
            # Not an error: the user has not asked for a proxy here. But their
            # machine has one, this download will not use it, and finding that
            # out from a connection timeout would be the wrong way.
            logger.warning(
                "provision: %s is set in the environment and will NOT be used "
                "- Elysium only uses the proxy configured in Settings. If this "
                "machine needs a proxy to reach the internet, set it there.",
                ", ".join(ambient_proxy_names()),
            )

        # NOT downloaded here. start_install is documented as "Begin. Returns
        # immediately", but the ~25 MB uv fetch ran inline on the request
        # thread: the POST did not answer until it finished (the frontend sets
        # no fetch timeout), while the job already reported running=true so the
        # UI drew a Cancel button that could not reach the download. It belongs
        # to the worker, which owns the cancel event.
        uv = find_uv()
    except ProvisionError:
        with _JOBS_LOCK:
            _JOBS.pop(engine_id, None)          # release the reserved slot
        raise

    threading.Thread(target=_install_worker, args=(job, p, uv),
                     name=f"tts-install-{engine_id}", daemon=True).start()
    return job.to_json()


def _install_worker(job: _Job, p: InstallPlan, uv: str | None) -> None:
    def log(line: str) -> None:
        job.log.append(line)
        del job.log[:-LOG_KEEP_LINES]

    if uv is None:
        job.state = "preparing"
        uv = _download_uv(log, cancel=job.cancel)
        if job.cancel.is_set():
            job.state = "cancelled"
            log("setup cancelled")
            job.finished_at = time.time()
            return
        if uv is None:
            _fail(job, TTS_PYTHON_NOT_FOUND,
                  "no way to build an isolated environment on this machine",
                  None, p.engine_id)
            job.finished_at = time.time()
            return

    target = Path(p.env_dir)
    # Build in a STAGING directory and swap only after verification. Building
    # in place would start by deleting the existing environment - so a failed
    # re-install (a repair, say) would destroy the working install it was
    # meant to fix, and leave the user worse off than before they clicked.
    staging = target.with_name(target.name + ".staging")
    python = staging / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    env = {
        # Same drive as the environment, or uv falls back from hardlinks to
        # full copies and the install costs roughly twice the disk.
        "UV_CACHE_DIR": str(config.TTS_UV_CACHE_DIR),
        "UV_PYTHON_INSTALL_DIR": str(config.TTS_PY_DIR),
        # The default 30 s read timeout is not enough for a 2.6 GB wheel on a
        # slow connection - it fails the whole install near the end.
        "UV_HTTP_TIMEOUT": "600",
        "PYTHONUTF8": "1",
    }
    # uv resolves and downloads from PyPI and download.pytorch.org; without
    # these it went direct even for a user who had configured a proxy.
    env = _proxy_env(env)
    spec = ENGINES.get(p.engine_id) or {}
    try:
        if staging.exists():
            log("clearing a leftover staging directory")
            shutil.rmtree(staging, ignore_errors=True)
        staging.parent.mkdir(parents=True, exist_ok=True)

        job.state = "installing"
        log(f"creating an isolated Python {p.python_version} environment")
        code, detail = _run(
            [uv, "venv", str(staging), "--python", p.python_version,
             "--no-project", "--seed"],
            on_line=log, cancel=job.cancel,
            timeout=float(config.TTS_INSTALL_TIMEOUT_S), env=env,
        )
        _check(job, code, detail)

        log("installing the engine and its dependencies")
        argv = [uv, "pip", "install", "--python", str(python),
                "-r", p.requirements]
        backend = spec.get("torch_backend")
        if backend:
            # Without this the resolver is free to take torch from PyPI, which
            # is the CPU build - a voice engine that cannot see the GPU.
            argv += [f"--torch-backend={backend}"]
        code, detail = _run(argv, on_line=log, cancel=job.cancel,
                            timeout=float(config.TTS_INSTALL_TIMEOUT_S), env=env)
        if code != 0 and not job.cancel.is_set() and "torch-backend" in detail:
            # An older uv rejects the flag outright. The pins carry explicit
            # +cu128 local versions, so pointing the resolver at the PyTorch
            # index is an equivalent (if slower to fail) way to say the same
            # thing - and PyPI cannot satisfy a +cu128 pin by accident.
            log("this uv does not know --torch-backend; retrying with the index")
            argv = [uv, "pip", "install", "--python", str(python),
                    "-r", p.requirements,
                    "--extra-index-url", "https://download.pytorch.org/whl/cu128"]
            code, detail = _run(argv, on_line=log, cancel=job.cancel,
                                timeout=float(config.TTS_INSTALL_TIMEOUT_S), env=env)
        _check(job, code, detail)

        job.state = "verifying"
        log("checking that the new environment actually works")
        _verify_env(p.engine_id, python, log, job.cancel)

        # Verified. Now - and only now - swap. RENAME-ASIDE, not delete-then-
        # replace (audit-2 C2): on Windows os.replace onto an existing
        # directory always raises, and rmtree(ignore_errors) over a locked
        # file (a worker, an antivirus scan) leaves a half-gutted env that
        # made the old code delete the VERIFIED staging in its error path.
        # Renaming the old env aside is atomic: if it is locked, it fails
        # cleanly HERE with both environments intact and the job reports a
        # coded error instead of destroying anything.
        old = target.with_name(target.name + ".old")
        shutil.rmtree(old, ignore_errors=True)   # leftover from a crash
        if target.exists():
            log("setting the previous environment aside")
            try:
                os.replace(str(target), str(old))
            except OSError as exc:
                raise ProvisionError(
                    TTS_RUNTIME_INSTALL_FAILED,
                    "the previous environment is in use and could not be "
                    "replaced - close anything using voice and retry",
                ) from exc
        try:
            os.replace(str(staging), str(target))
        except OSError as exc:
            # Put the old one back; the verified staging survives for retry.
            try:
                if old.exists():
                    os.replace(str(old), str(target))
            except OSError:
                logger.warning("tts: could not restore the previous env")
            raise ProvisionError(
                TTS_RUNTIME_INSTALL_FAILED,
                "could not move the new environment into place",
            ) from exc
        shutil.rmtree(old, ignore_errors=True)
        runtimes.register(p.engine_id, str(env_python(p.engine_id)),
                          installed_at=time.time(), python_version=p.python_version)
        job.state = "done"
        log("voice engine ready")
    except ProvisionError as exc:
        _fail(job, exc.code, exc.detail, staging, p.engine_id)
    except _Cancelled:
        job.state = "cancelled"
        job.error_code = None
        log("setup cancelled")
        # Only the staging is removed: a cancel must never cost the user an
        # environment that was working before they clicked install.
        shutil.rmtree(staging, ignore_errors=True)
    except Exception as exc:                    # noqa: BLE001
        logger.exception("tts: install failed")
        _fail(job, TTS_RUNTIME_INSTALL_FAILED, f"{type(exc).__name__}: {exc}"[:300],
              staging, p.engine_id)
    finally:
        job.finished_at = time.time()


class _Cancelled(Exception):
    pass


def _check(job: _Job, code: int, detail: str) -> None:
    if job.cancel.is_set():
        raise _Cancelled()
    if code != 0:
        raise ProvisionError(TTS_RUNTIME_INSTALL_FAILED, detail or "the installer failed")


def _fail(job: _Job, code: str, detail: str, staging: Path | None,
          engine_id: str) -> None:
    """A failed job removes only what IT built.

    The staging directory goes; a pre-existing working environment and its
    registration stay untouched, so a failed repair can never leave the user
    worse off than before they clicked. Nothing was registered for the new
    build yet (registration is the last step), so there is nothing to undo.
    """
    job.state = "failed"
    job.error_code = code
    job.error_detail = detail
    job.log.append(f"setup failed: {detail}")
    # None when the job failed BEFORE a staging directory existed (the uv
    # download, which now runs in the worker rather than blocking the request).
    if staging is not None:
        shutil.rmtree(staging, ignore_errors=True)


def job(engine_id: str) -> dict:
    with _JOBS_LOCK:
        existing = _JOBS.get(engine_id)
    return existing.to_json() if existing else _Job(engine_id=engine_id).to_json()


def all_jobs(engine_ids: list[str]) -> list[dict]:
    return [job(e) for e in engine_ids]


def cancel(engine_id: str) -> dict:
    with _JOBS_LOCK:
        existing = _JOBS.get(engine_id)
    if existing is None or not existing.to_json()["running"]:
        return job(engine_id)
    existing.cancel.set()
    return existing.to_json()


def cancel_all() -> None:
    """Stop every running install. Registered as a process-teardown hook: the
    installer drives a real uv subprocess, and a daemon thread dying with the
    app does NOT take that subprocess with it - without this, closing the
    window mid-install leaves uv downloading gigabytes into the void."""
    with _JOBS_LOCK:
        jobs = list(_JOBS.values())
    for existing in jobs:
        if existing.to_json()["running"]:
            existing.cancel.set()


def _known(engine_id: str) -> str:
    """Defence in depth: these ids become filesystem paths handed to rmtree.
    The router validates too, but a path must never depend on one caller."""
    if engine_id not in ENGINES:
        raise ProvisionError(TTS_ENGINE_UNKNOWN, f"unknown engine {engine_id}")
    return engine_id


def uninstall(engine_id: str) -> dict:
    """Give the disk back. The same one action that installed it removes it."""
    _known(engine_id)
    with _JOBS_LOCK:
        existing = _JOBS.get(engine_id)
        if existing is not None and existing.to_json()["running"]:
            raise ProvisionError(TTS_RUNTIME_INSTALLING,
                                 "setup is running; cancel it first")
    # Reserve the slot for the WHOLE removal (audit-2 C8): the old code
    # checked once, spent a multi-GB rmtree outside the lock, then popped
    # whatever job held the slot by then - including a fresh install's.
    marker = _Job(engine_id=engine_id, state="uninstalling",
                  started_at=time.time())
    with _JOBS_LOCK:
        existing = _JOBS.get(engine_id)
        if existing is not None and existing.to_json()["running"]:
            raise ProvisionError(TTS_RUNTIME_INSTALLING,
                                 "setup is running; cancel it first")
        _JOBS[engine_id] = marker
    try:
        target = env_dir(engine_id)
        existed = target.exists()
        shutil.rmtree(target, ignore_errors=True)
        shutil.rmtree(target.with_name(target.name + ".staging"), ignore_errors=True)
        shutil.rmtree(target.with_name(target.name + ".old"), ignore_errors=True)
        # ONLY when the files are actually gone. Unregistering regardless made
        # a failed uninstall unrecoverable through the UI: the engine dropped
        # out of the runtimes list, so the panel stopped drawing it, so the
        # Remove button that would retry no longer existed - while several GB
        # sat on the disk the user had just asked to get back. Staying
        # registered is what keeps the retry reachable.
        if not target.exists():
            try:
                runtimes.unregister(engine_id)
            except Exception:                   # noqa: BLE001
                logger.warning("tts: could not unregister %s on uninstall", engine_id)
        else:
            logger.warning(
                "tts: %s could not be fully removed (files still at %s); left "
                "registered so the uninstall can be retried.",
                engine_id, target.name,
            )
    finally:
        with _JOBS_LOCK:
            if _JOBS.get(engine_id) is marker:
                _JOBS.pop(engine_id, None)
    # When the LAST environment goes, the uv wheel cache - by far the largest
    # leftover - goes with it. "Give the disk back" has to mean all of it.
    try:
        envs = Path(config.TTS_ENVS_DIR)
        if not envs.exists() or not any(envs.iterdir()):
            shutil.rmtree(config.TTS_UV_CACHE_DIR, ignore_errors=True)
    except OSError:
        pass
    removed = existed and not target.exists()
    return {"engine_id": engine_id, "removed": removed}
