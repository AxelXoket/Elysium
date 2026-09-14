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

    Where a package's own metadata contradicts the freeze it was measured
    in, a short overrides file beside the freeze says so and the resolver is
    told to believe the freeze. install_argv carries the measurement
    behind that. One engine, Fish, is not a package at all but a source tree;
    _install_source_tree says why and how it is pinned.

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
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import config
import launch_token
import secure_delete

from . import runtimes
from ._which import which_trusted
from .errors import (
    TTS_ENGINE_UNKNOWN,
    TTS_INSUFFICIENT_DISK,
    TTS_PROXY_UNAVAILABLE,
    TTS_PYTHON_NOT_FOUND,
    TTS_RUNTIME_INSTALLING,
    TTS_RUNTIME_INSTALL_FAILED,
)

logger = logging.getLogger(__name__)


def _rmtree(path) -> None:
    """`shutil.rmtree`, refusing a redirected name OUT LOUD.

    Every tree this module deletes lives under the app's own data folder -
    `TTS_ENVS_DIR/<engine>`, its `.staging` and `.old` siblings, and
    `TTS_UV_CACHE_DIR`. Ordinary directories, created by the app, writable by
    the user it runs as, and deleted recursively. So all nine are names a
    `mklink /J` can point somewhere else, with no privileges needed.

    WHAT THIS DOES NOT FIX, measured rather than assumed. `shutil.rmtree`
    already refuses: on a junction it raises `OSError("Cannot call rmtree on
    a symbolic link")` and deletes nothing. The nine call sites were never
    deleting through a junction, and any claim that they were is wrong.

    What they WERE doing is passing `ignore_errors=True`, which swallows that
    refusal whole. Nothing is deleted, nothing is logged, and the caller
    proceeds as if the tree were gone - to unpack a fresh environment onto a
    name that still points at somebody else's folder. The failure is one line
    later than it looks, and it is silent, which is why it needs a voice
    rather than another guard.

    `is_dir()` first: `is_redirected` fails CLOSED on any OSError, ENOENT
    included, so a path that simply does not exist answers True and an
    unguarded check would refuse every fresh install.
    """
    p = Path(path)
    if p.is_dir() and secure_delete.is_redirected(p):
        logger.warning(
            "tts: refusing to delete through a redirected name")
        return
    shutil.rmtree(p, ignore_errors=True)

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000
LOG_KEEP_LINES = 400

# The full patch, and uv is told to use its own managed build of it (see the
# venv command in _install_worker). "3.12" alone took whatever 3.12.x the
# machine had or uv knew, which left the interpreter the one component of the
# environment with no pin; the measured installs of 8 September 2026 ran on
# CPython 3.12.10.
PYTHON_VERSION = "3.12.10"

# What each engine must be able to import before we call it ready, and roughly
# how much it will pull down (shown before the user commits to the wait).
#
# The verify lists name what the WORKER SCRIPTS actually import at load time,
# module for module, not the engine's top-level package. The difference is
# not pedantry: `import fish_speech` passes on a directory that has no
# __init__.py at all, because Python treats it as a namespace package, so it
# proved nothing in the very environment where the worker's real imports
# (fish_speech.models.dac.inference and its sibling) would have failed. Each
# worker names what it binds in its own ENGINE_IMPORTS tuple, and
# test_tts_verify_contract holds every list below to a superset of it. The
# lists carry a few names beyond the tuples: chatterbox and TTS by their
# package names, torchaudio and torchao, which the workers reach through
# what they bind, and fish's speed path, torchao.quantization (the fp8
# quantiser, imported at load) and
# triton (what torch.compile needs on Windows); without those two the engine
# runs, slowly, and the proof would rather know at install time.
ENGINES: dict[str, dict] = {
    "fish_s2": {
        "verify": ["torch", "torchaudio", "torchao", "torchao.quantization",
                   "triton", "numpy", "soundfile",
                   "fish_speech.models.dac.inference",
                   "fish_speech.models.text2semantic.inference"],
        "download_mb": 4200,
        "torch_backend": "cu128",
        # fish_speech is not on PyPI, and it is not installable as a wheel
        # either: its pyproject names two top-level packages and no
        # subpackages, so the wheel uv builds from the archive has none of
        # fish_speech/models, no configs/ and no .project-root, all three of
        # which the worker's imports need. The environment that was measured
        # working ran an EDITABLE install, which is a source tree on sys.path.
        # This reproduces that: the pinned archive is fetched, its extracted
        # tree is checked against a content digest, placed under the
        # environment, and reached through one .pth line: the tree root on
        # sys.path, which is what the editable install amounted to at run
        # time. The archive's other top-level folders (tools, docs) become
        # importable too, after site-packages, so nothing is shadowed. See
        # _install_source_tree.
        "source_tree": {
            "url": ("https://github.com/fishaudio/fish-speech/archive/"
                    "e5e292632cb11e7a27b2b7487f58f612bc101e13.tar.gz"),
            # Digest of the extracted TREE (paths and bytes), not of the
            # archive: GitHub builds commit archives on the fly and has
            # changed the compression once before, which would fail a
            # byte pin on an archive whose contents had not moved.
            "tree_sha256": "4ad6357dfcd936354d289d6f74e597dd7f96152b8e31ed7d632085182d3c140c",
            "dir": "fish-speech",
            "pth": "elysium-fish-speech.pth",
            # The archive is 6.8 MB and extracts to 8.7 MB. A cap far above
            # that and far below "fill the disk".
            "max_bytes": 64 * 1024 * 1024,
        },
    },
    "xtts_v2": {
        "verify": ["torch", "torchaudio", "numpy", "TTS",
                   "TTS.tts.configs.xtts_config", "TTS.tts.models.xtts"],
        "download_mb": 3400,
        "torch_backend": "cu128",
    },
    "chatterbox": {
        "verify": ["torch", "torchaudio", "numpy", "librosa", "perth",
                   "chatterbox", "chatterbox.tts", "chatterbox.mtl_tts"],
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


_EXACT_PIN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?==[A-Za-z0-9.+!]+$")
_DIRECT_URL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]* @ https://\S+$")
_IMMUTABLE_REF = re.compile(r"#sha256=[0-9a-f]{64}$")


def build_constraints_path() -> Path:
    """One file for all engines: the build backends the source-archive pins
    are built with. See the file's own header."""
    return Path(__file__).resolve().parent / "requirements" / "build-constraints.txt"


def build_constraint_lines() -> list[str]:
    return _lines_of(build_constraints_path())


def overrides_path(engine_id: str) -> Path:
    """Beside the freeze, same stem, `.overrides.txt`. Present only for an
    engine whose freeze a resolver would refuse; see install_argv."""
    return Path(__file__).resolve().parent / "requirements" / f"{engine_id}.overrides.txt"


def _lines_of(path: Path) -> list[str]:
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def requirement_lines(engine_id: str) -> list[str]:
    """The lines the installer hands uv: comments and blanks removed, nothing
    else touched. A direct-URL line keeps its `#sha256=` fragment, which is
    why this does not strip from the first `#` it sees."""
    return _lines_of(requirements_path(engine_id))


def override_lines(engine_id: str) -> list[str]:
    """The overrides for an engine, or [] when it has no overrides file."""
    path = overrides_path(engine_id)
    return _lines_of(path) if path.is_file() else []


def is_exact_pin(line: str) -> bool:
    """Whether one requirement line names exactly one artefact.

    A freeze is only a freeze while nothing in it can float: either an exact
    `==` pin, or a direct URL that ends in a content hash. A commit id in a
    URL is not enough: GitHub builds commit archives on the fly and has
    changed their bytes before. A range, a bare name, a branch name or a tag
    is refused here, and test_tts_provision holds every shipped file to this.
    """
    if _EXACT_PIN.match(line):
        return True
    if _DIRECT_URL.match(line):
        url = line.split(" @ ", 1)[1]
        return bool(_IMMUTABLE_REF.search(url))
    return False


def venv_argv(uv: str, target, python_version: str) -> list[str]:
    """The command that creates an engine's interpreter. One builder, used by
    the install worker and by verify/verify_tts_resolve.py.

    --managed-python: uv's own build of the pinned patch version, downloaded
    into TTS_PY_DIR when absent, never the machine's Python. --no-config for
    the same reason install_argv gives. No --seed: it installed pip from the
    index at whatever version was current that day, unpinned and unhashed,
    and nothing here ever runs pip.
    """
    return [str(uv), "venv", str(target), "--python", python_version,
            "--no-project", "--managed-python", "--no-config"]


def child_env_base() -> dict:
    """What every uv child is handed before the proxy overlay: our cache and
    interpreter folders, a read timeout that survives a 2.6 GB wheel, UTF-8."""
    return {
        # Same drive as the environment, or uv falls back from hardlinks to
        # full copies and the install costs roughly twice the disk.
        "UV_CACHE_DIR": str(config.TTS_UV_CACHE_DIR),
        "UV_PYTHON_INSTALL_DIR": str(config.TTS_PY_DIR),
        # The default 30 s read timeout is not enough for a 2.6 GB wheel on a
        # slow connection - it fails the whole install near the end.
        "UV_HTTP_TIMEOUT": "600",
        "PYTHONUTF8": "1",
    }


def install_argv(uv: str, python, p: InstallPlan) -> list[str]:
    """The exact command that turns a freeze file into an environment.

    One builder, used by the install worker and by verify/verify_tts_resolve.py,
    so what the periodic check resolves is what the user's machine runs.

    WHY AN OVERRIDES FILE. Each requirements file is a complete `uv pip
    freeze` of an environment that was measured working. Handed back to the
    resolver as a request, two of the three were refused, because a package's
    own metadata contradicts the versions it was measured beside:
    chatterbox-tts 0.1.7 declares torch 2.6.0 where the measured environment
    runs 2.8.0, descript-audiotools 0.7.2 declares protobuf below 3.20 where
    5.29 is what works. Measured 8 September 2026 with uv 0.11.7: fish_s2 and
    chatterbox unsatisfiable, only xtts_v2 resolved, which means the one-click
    install had never been able to finish for two of the three engines. An
    overrides file names exactly those contradicted requirements and tells the
    resolver to take the freeze's word for them. Everything else is still
    resolved and checked, which is why this and not `--no-deps`: a freeze
    line that went stale or a pin that lost its +cu128 suffix still fails at
    install time, where it is loud, instead of at the first load, where it is
    a user's afternoon. The suite holds every override to an exact pin that
    also appears verbatim in the freeze, so an override can only ever repeat
    the freeze, never introduce a version.

    WHY --torch-backend. Left alone, the resolver takes torch from PyPI, which
    is the CPU build: a voice engine that cannot see the GPU. The flag sends
    the torch family to the CUDA index. It also sends torchao there, and that
    index carries no Windows wheel for torchao, so fish_s2.txt pins torchao by
    direct URL and hash to the PyPI file the measured environment runs.

    WHY --no-config. uv reads uv.toml from the working directory chain and
    from the user's config directory, and either can name another index. The
    child is handed a curated environment (_proxy_env); a config file is the
    same redirect through a different door.

    There is no fallback for a uv that does not know --torch-backend: find_uv
    refuses a uv older than the pin and the installer fetches its own.
    """
    argv = [str(uv), "pip", "install", "--no-config", "--python", str(python),
            "-r", p.requirements,
            # The build backend for the pins that arrive as source archives;
            # otherwise it is fetched unpinned at install time.
            "--build-constraints", str(build_constraints_path())]
    overrides = overrides_path(p.engine_id)
    if overrides.is_file():
        argv += ["--overrides", str(overrides)]
    backend = (ENGINES.get(p.engine_id) or {}).get("torch_backend")
    if backend:
        argv += [f"--torch-backend={backend}"]
    return argv


# ── uv ───────────────────────────────────────────────────────────────────────

_UV_VERSION_RE = re.compile(r"\buv (\d+)\.(\d+)\.(\d+)")


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _uv_version(path: str) -> tuple[int, ...] | None:
    """What `uv --version` says, or None when it cannot be asked.

    Through _run, never a bare subprocess call: _run strips the launch token
    from the child (a uv found on PATH is not this app's code), hides the
    window, and kills the whole process tree at the deadline, which a shim
    that hands the real work to a grandchild would otherwise hold open past
    any timeout. Still on the request thread, so the deadline is short.
    """
    lines: list[str] = []
    code, _ = _run([path, "--version"], on_line=lines.append,
                   cancel=threading.Event(), timeout=15)
    if code != 0:
        return None
    for line in lines:
        match = _UV_VERSION_RE.search(line)
        if match:
            return tuple(int(g) for g in match.groups())
    return None


def find_uv() -> str | None:
    """Our own copy first, then a machine uv of exactly the pinned version.

    Never `sys._MEIPASS`: in a onefile build that is a fresh temp directory
    extracted per launch and deleted on exit, so anything installed from there
    would point at a path that stops existing.

    The version gate replaces a fallback. A uv older than the pin does not
    know --torch-backend, and the retry that used to cover it pointed the
    resolver at the PyTorch index with --extra-index-url, which in uv ranks
    ABOVE PyPI for every name that index serves: the same name==version could
    then arrive from a different origin than the one the freeze was measured
    against, with nothing to notice. Fetching the pinned uv instead costs a
    25 MB download once and runs the one command every measured install ran.
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
    if not found:
        home = Path.home() / ".local" / "bin" / name
        found = str(home) if home.is_file() else None
    if not found:
        return None
    version = _uv_version(found)
    # Exactly the pin, not "at least": the interpreter manifest that
    # --managed-python resolves "3.12.10" against is embedded in uv, and so is
    # the list of packages --torch-backend re-routes, so a newer uv can build
    # a different environment from the same command. The pinned copy is a
    # 25 MB download once.
    if version is None or version != _version_tuple(UV_VERSION):
        return None
    return found


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


#: What the gate says when it refuses. A constant rather than a literal
#: repeated at each raise site, so the tests that assert on the refusal import
#: the sentence instead of retyping it and going stale against it.
PROXY_REQUIRED_UNAVAILABLE = "a proxy is required but none is available"


def _egress_gate() -> str | None:
    """THE gate. Every byte this module sends out passes through here.

    Returns the proxy to route through, or None when none is configured and
    none is required. Raises ProxyUnreadable when the user armed the mandatory
    proxy and this process cannot produce one - the vault is locked, or
    nothing is set. Refusing is the whole point: a setting whose purpose is
    "do not go out without the proxy" cannot answer "we could not read it" by
    going out without the proxy.

    It is one function because it used to be two answers. The installer has
    exactly two ways onto the network - its own download of uv, and the uv
    subprocess that fetches several gigabytes of packages - and they decided
    this separately. The download side (then `_url_opener`, a name that no
    longer exists) refused. `_proxy_env` called `_proxy_url`,
    which swallows ProxyUnreadable into None, saw no proxy, and quietly set no
    proxy variables at all, so the child went out bare. Same module, same
    state, opposite behaviour, and the fail-open one was the multi-gigabyte
    path - and the only one that runs at all on a machine that already has uv.

    So the rule is not "each caller checks". It is that neither caller is
    allowed to know how to answer this: the proxy arrives from here or the
    call does not happen.
    """
    proxy = _proxy_url()
    if not proxy and _proxy_required():
        raise ProxyUnreadable(PROXY_REQUIRED_UNAVAILABLE)
    return proxy


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
    # The rest of uv's resolver surface. Each of these changes what a pin
    # means or where a download comes from without touching the command
    # line: an override file replaces versions, a constraint or exclude
    # narrows them, the index strategy decides which index wins a name, the
    # torch backend re-routes the torch family, the config file can name an
    # index, and the two mirrors redirect where the interpreter itself is
    # fetched from. Measured 8 September 2026: none were stripped, and
    # UV_OVERRIDE in particular replaces a pin under every flag this
    # installer passes.
    "UV_OVERRIDE", "UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_EXCLUDE",
    "UV_INDEX_STRATEGY", "UV_TORCH_BACKEND", "UV_NO_BUILD_ISOLATION",
    "UV_NO_INDEX", "UV_CONFIG_FILE", "UV_PYTHON_INSTALL_MIRROR",
    "UV_PYPY_INSTALL_MIRROR",
    # TLS trust. UV_INSECURE_HOST makes uv accept any certificate from the
    # hosts it names, SSL_CERT_FILE and SSL_CLIENT_CERT replace the bundle
    # and the client identity, UV_NATIVE_TLS swaps the trust store. Any of
    # them in a login script and an on-path attacker can hand the resolver a
    # wheel that satisfies an exact pin; the one hash in the freezes is
    # torchao's, and UV_NO_VERIFY_HASHES turns even that off. Measured
    # 8 September 2026 against uv 0.11.7's own --help.
    "UV_INSECURE_HOST", "SSL_CERT_FILE", "SSL_CLIENT_CERT", "UV_NATIVE_TLS",
    "UV_NO_VERIFY_HASHES", "UV_KEYRING_PROVIDER",
    # Which interpreter. The venv command asks for uv's managed build of the
    # pinned version; these could point it at a system Python or forbid the
    # download that makes the pin mean something.
    "UV_PYTHON", "UV_MANAGED_PYTHON", "UV_NO_MANAGED_PYTHON",
    "UV_PYTHON_DOWNLOADS", "UV_PYTHON_PREFERENCE",
    # The interpreter download itself: a manifest that names the URL and
    # the hash of every build uv will install outranks the mirrors above,
    # and the build tag picks a different python-build-standalone release of
    # the same "3.12.10".
    "UV_PYTHON_DOWNLOADS_JSON_URL", "UV_PYTHON_CPYTHON_BUILD", "UV_PYTHON_CACHE_DIR",
    # The native trust store switch by its current name (UV_NATIVE_TLS above
    # is the old alias), the CA directory, and the two that rebuild a pin
    # from source with an unpinned build backend.
    "UV_SYSTEM_CERTS", "SSL_CERT_DIR", "UV_NO_BINARY", "UV_NO_BINARY_PACKAGE",
    # The venv command's own switches: a seed puts pip from the index into
    # every environment, which removing --seed closed.
    "UV_VENV_SEED", "UV_VENV_CLEAR", "UV_VENV_RELOCATABLE",
    # Resolution and trust knobs read from the environment: a cutoff date
    # that turns a wheel into a source build, the resolution and prerelease
    # strategies, two checks on downloaded archives, and two that move the
    # cache or link into it.
    "UV_EXCLUDE_NEWER", "UV_RESOLUTION", "UV_PRERELEASE", "UV_FORK_STRATEGY",
    "UV_INSECURE_NO_ZIP_VALIDATION", "UV_SKIP_WHEEL_FILENAME_CHECK",
    "UV_NO_CACHE", "UV_LINK_MODE",
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

    Raises ProxyUnreadable, from the gate, when a mandatory proxy cannot be
    produced. That is a change of kind, not of degree: this function used to
    return an environment with every proxy variable REMOVED and none put back,
    which is the most direct instruction there is to go out bare, handed to
    the child that downloads the most.
    """
    env = {**base, **{name: None for name in _ENV_NETWORK_STRIP}}
    proxy = _egress_gate()
    if proxy:
        env.update({
            "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy,
            "http_proxy": proxy, "https_proxy": proxy, "all_proxy": proxy,
        })
    return env


def _provision_client():
    """A client for this module's own download, built THROUGH the gate.

    The gate runs first and is allowed to refuse: start_install's check
    happens before the job is queued, and the vault can lock between there and
    this ~25 MB request to GitHub, which would otherwise go out bare from a
    machine whose user made the proxy mandatory.

    The client itself is built in network_client, which is the one file
    allowed to construct one (test_egress_chokepoint's
    TestNothingElseBuildsAClient), so this download passes a host allowlist
    the same way every other outbound request in the app does. It used to be a
    hand-rolled urllib opener that no allowlist ever saw, and urllib's
    ProxyHandler speaks only `http://` - measured, a user with `socks5h://`
    got plain CONNECT bytes at their SOCKS port and the resulting failure was
    reported to them as a broken machine.
    """
    from network_client import provisioning_client

    return provisioning_client(_egress_gate())


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
        # Through the CONFIGURED proxy, and only if the gate allows it at all
        # (see _provision_client): this used to be the default urllib opener,
        # so a user with a proxy - even a mandatory one - had their real IP
        # contact github.com the moment they pressed Set up.
        client = _provision_client()
        try:
            with client.stream("GET", UV_URL, timeout=120) as resp, \
                    open(archive, "wb") as out:
                resp.raise_for_status()
                digest = hashlib.sha256()
                # Cancel used to be a no-op here: the job was already in _JOBS
                # with running=true so the UI drew its Cancel button, pressing
                # it set this event and returned 200 - and this loop never
                # looked, so the download ran to completion and the button
                # appeared to have done nothing. Checked before the first read
                # as well as between chunks, so a cancel that arrives while
                # the server is still thinking is not held until data does.
                if cancel is not None and cancel.is_set():
                    on_line("uv download cancelled")
                    return None
                for chunk in resp.iter_bytes(1024 * 256):
                    if cancel is not None and cancel.is_set():
                        on_line("uv download cancelled")
                        return None
                    digest.update(chunk)
                    out.write(chunk)
        finally:
            client.close()
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
    except ProxyUnreadable:
        # Out past the generic handler below, deliberately. Swallowed into
        # `return None`, this arrived at the caller as "no uv" and became
        # TTS_PYTHON_NOT_FOUND - "Elysium could not find a way to build the
        # voice engine environment on this computer" - which blames the
        # machine for the user's own proxy setting and sends them looking at
        # their hardware for a locked vault.
        on_line("the setup needs your proxy and could not get it")
        raise
    except Exception as exc:                    # noqa: BLE001
        on_line(f"could not fetch uv: {type(exc).__name__}")
        return None
    finally:
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            pass


#: More members than any engine tree this app pins could have (fish's is
#: 192 files in 44 folders). The size cap does not bound a tar of empty
#: files; this does.
MAX_TREE_MEMBERS = 5000


def _tree_digest(root: Path) -> str:
    """SHA-256 over a directory's CONTENT: every regular file, in sorted
    relative path order, as path, size and bytes. Timestamps, permissions and
    the archive's own compression are outside it on purpose, which is what
    makes a pin on this survive GitHub re-packing an unchanged commit."""
    digest = hashlib.sha256()
    # Ordered by the posix relative path as a string, so the digest is the
    # same on every platform: sorting Path objects folds case on Windows and
    # puts fish_speech before LICENSE there and after it elsewhere.
    files = sorted((f for f in root.rglob("*") if f.is_file()),
                   key=lambda f: f.relative_to(root).as_posix())
    for f in files:
        rel = f.relative_to(root).as_posix().encode("utf-8")
        digest.update(rel + b"\0" + str(f.stat().st_size).encode() + b"\0")
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _site_packages(python: Path, on_line, cancel) -> Path:
    """Where the new interpreter reads .pth files from, asked of the
    interpreter itself rather than guessed from the platform."""
    lines: list[str] = []
    # -X utf8 as well as -I: under -I the interpreter ignores PYTHONUTF8 and
    # prints in the ANSI code page, _run decodes UTF-8, and a profile name
    # with a non-ASCII letter came back with a replacement character in it,
    # a folder that does not exist. Measured 8 September 2026.
    code, detail = _run(
        [str(python), "-I", "-X", "utf8", "-c",
         "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        on_line=lines.append, cancel=cancel, timeout=120,
    )
    answer = next((ln.strip() for ln in reversed(lines) if ln.strip()), "")
    if code != 0 and cancel.is_set():
        raise _Cancelled()
    if code != 0 or not answer:
        raise ProvisionError(TTS_RUNTIME_INSTALL_FAILED,
                             f"the new environment could not say where its "
                             f"site-packages is: {detail}"[:300])
    return Path(answer)


def _install_source_tree(spec: dict, staging: Path, python: Path, on_line,
                         cancel: threading.Event) -> None:
    """Fetch a pinned source archive, verify its content, put it on the new
    interpreter's path. The editable install, done by hand and pinned.

    Fish is the reason this exists (see ENGINES["fish_s2"]["source_tree"]).
    Building the archive into a wheel loses the subpackages and data files
    the worker imports, and an editable install through uv would pull an
    unpinned build backend from PyPI at install time. Neither is the
    environment that was measured. This is: the tree the archive holds,
    reachable through one .pth path line, the plain `develop` mechanism,
    without the build step.

    Every step is bounded. The download stops at `max_bytes`; the archive is
    extracted with tarfile's data filter, which refuses absolute paths, parent
    references, links that point outside the tree, and device nodes; the
    member count and the unpacked size are capped; the extracted tree is digested and
    compared to the pin BEFORE it is moved into place, and a mismatch removes
    it and fails the install. Nothing here is imported or executed; the first
    code from the tree runs in _verify_env, in the new interpreter.
    """
    url = spec["url"]
    dest = staging / "src" / spec["dir"]
    scratch = staging / "src" / f".extract.{os.getpid()}.{threading.get_ident()}"
    archive = staging / "src" / f".archive.{os.getpid()}.{threading.get_ident()}.tar.gz"
    max_bytes = int(spec["max_bytes"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        on_line(f"fetching the engine's source tree ({url.rsplit('/', 1)[-1]})")
        client = _provision_client()
        try:
            with client.stream("GET", url, timeout=120) as resp, \
                    open(archive, "wb") as out:
                resp.raise_for_status()
                total = 0
                if cancel.is_set():
                    raise _Cancelled()
                for chunk in resp.iter_bytes(1024 * 256):
                    if cancel.is_set():
                        raise _Cancelled()
                    total += len(chunk)
                    if total > max_bytes:
                        raise ProvisionError(
                            TTS_RUNTIME_INSTALL_FAILED,
                            "the engine's source archive is larger than the "
                            "pinned one could be - refused")
                    out.write(chunk)
        finally:
            client.close()

        on_line("unpacking and checking the source tree")
        if scratch.exists():
            _rmtree(scratch)
        scratch.mkdir(parents=True)
        try:
            with tarfile.open(archive, "r:gz") as tar:
                # The cap on the download bounds the archive; this bounds what
                # it unpacks to, header by header, so a few kilobytes of gzip
                # standing for gigabytes of zeros or for millions of empty
                # members is refused at the first header past the cap rather
                # than after every header has been read into memory. The
                # pinned tree is 192 files in 44 folders, under nine megabytes.
                unpacked = 0
                members = 0
                for member in tar:
                    members += 1
                    if member.isfile():
                        unpacked += member.size
                    if unpacked > max_bytes or members > MAX_TREE_MEMBERS:
                        raise ProvisionError(
                            TTS_RUNTIME_INSTALL_FAILED,
                            "the engine's source archive unpacks to more than "
                            "the pinned tree could be - refused")
                tar.extractall(scratch, filter="data")
        except (tarfile.TarError, OSError, ValueError) as exc:
            raise ProvisionError(TTS_RUNTIME_INSTALL_FAILED,
                                 f"the engine's source archive could not be "
                                 f"unpacked: {type(exc).__name__}") from exc
        if cancel.is_set():
            raise _Cancelled()
        tops = [d for d in scratch.iterdir()]
        if len(tops) != 1 or not tops[0].is_dir():
            raise ProvisionError(TTS_RUNTIME_INSTALL_FAILED,
                                 "the engine's source archive did not hold "
                                 "exactly one directory")
        digest = _tree_digest(tops[0])
        if digest != str(spec["tree_sha256"]).lower():
            raise ProvisionError(TTS_RUNTIME_INSTALL_FAILED,
                                 "the engine's source tree did not match its "
                                 "pinned checksum - refused")
        if dest.exists():
            _rmtree(dest)
        os.replace(str(tops[0]), str(dest))

        site = _site_packages(python, on_line, cancel)
        # A .pth is a list of directories site adds to sys.path at startup,
        # read from site-packages only. One line, the tree's location, is the
        # whole mechanism the editable install used. RELATIVE to the
        # site-packages directory, never absolute: site.py joins a relative
        # line onto the directory the .pth sits in, and this environment is
        # built under a staging name that is renamed into place only after
        # the proof. Measured 8 September 2026: an absolute line named the
        # staging directory, the proof passed there, and the first load after
        # the rename found no tree and exited 3, which the app reported as
        # "its files are gone now, set up again". A relative line moves with
        # the environment.
        (site / spec["pth"]).write_text(
            os.path.relpath(dest, site) + "\n", encoding="utf-8")
        on_line("source tree in place")
    except ProxyUnreadable as exc:
        raise ProvisionError(TTS_PROXY_UNAVAILABLE, str(exc)) from exc
    except (ProvisionError, _Cancelled):
        raise
    except Exception as exc:                    # noqa: BLE001
        raise ProvisionError(TTS_RUNTIME_INSTALL_FAILED,
                             f"could not fetch the engine's source tree: "
                             f"{type(exc).__name__}") from exc
    finally:
        for leftover in (archive, scratch):
            try:
                if leftover.is_dir():
                    _rmtree(leftover)
                else:
                    leftover.unlink(missing_ok=True)
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
            [str(python), "-I", "-X", "utf8", "-c", f"import {module}"],
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
                    TTS_PROXY_UNAVAILABLE,
                    "a proxy is required but the vault could not be read - "
                    "unlock it and try again",
                ) from None
            if not configured:
                raise ProvisionError(
                    TTS_PROXY_UNAVAILABLE,
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
        log(f"no uv {UV_VERSION} or newer on this machine; fetching the pinned copy")
        try:
            uv = _download_uv(log, cancel=job.cancel)
        except ProxyUnreadable as exc:
            # The gate refused this module's own download. Its own code, so
            # the sentence the user reads names the proxy instead of the
            # machine, and staging is None because nothing has been built yet.
            _fail(job, TTS_PROXY_UNAVAILABLE, str(exc), None, p.engine_id)
            job.finished_at = time.time()
            return
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
    env = child_env_base()
    # uv resolves and downloads from PyPI and download.pytorch.org; without
    # these it went direct even for a user who had configured a proxy. This is
    # the OTHER egress path, the one worth several gigabytes, and it reaches
    # the same gate the download above did - which is the whole point, because
    # these two used to disagree and this was the one that said yes.
    try:
        env = _proxy_env(env)
    except ProxyUnreadable as exc:
        # Before the venv is created, so there is no staging directory to
        # remove and nothing was started that has to be undone.
        _fail(job, TTS_PROXY_UNAVAILABLE, str(exc), None, p.engine_id)
        job.finished_at = time.time()
        return
    try:
        if staging.exists():
            log("clearing a leftover staging directory")
            _rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)

        job.state = "installing"
        log(f"creating an isolated Python {p.python_version} environment")
        code, detail = _run(
            venv_argv(uv, staging, p.python_version),
            on_line=log, cancel=job.cancel,
            timeout=float(config.TTS_INSTALL_TIMEOUT_S), env=env,
        )
        _check(job, code, detail)

        log("installing the engine and its dependencies")
        # The freeze, with its overrides; install_argv says why at length.
        argv = install_argv(uv, python, p)
        code, detail = _run(argv, on_line=log, cancel=job.cancel,
                            timeout=float(config.TTS_INSTALL_TIMEOUT_S), env=env)
        _check(job, code, detail)

        tree = (ENGINES.get(p.engine_id) or {}).get("source_tree")
        if tree:
            _install_source_tree(tree, staging, python, log, job.cancel)

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
        _rmtree(old)   # leftover from a crash
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
        _rmtree(old)
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
        _rmtree(staging)
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
        _rmtree(staging)


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
        _rmtree(target)
        _rmtree(target.with_name(target.name + ".staging"))
        _rmtree(target.with_name(target.name + ".old"))
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
    # leftover - goes with it. "Give the disk back" has to mean all of it,
    # for THIS engine: the uv under bin/ and the interpreter under python/
    # serve every engine and stay.
    try:
        envs = Path(config.TTS_ENVS_DIR)
        if not envs.exists() or not any(envs.iterdir()):
            _rmtree(config.TTS_UV_CACHE_DIR)
    except OSError:
        pass
    removed = existed and not target.exists()
    return {"engine_id": engine_id, "removed": removed}
