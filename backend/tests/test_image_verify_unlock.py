"""verify_image_output.py could never reach the network, and nobody noticed.

Its precondition was `vault_state.is_unlocked()`, which reads THIS process's
memory. The key only ever lives in the running server's process, so a fresh
CLI run always started locked and the script printed "unlock Elysium once, then
re-run" - an instruction describing something that cannot work. Unlocking the
desktop app does nothing for a separately launched Python process; they share
no memory.

It now unlocks itself from a getpass prompt. These tests cover the two paths
that must never cost money: no passphrase, and a wrong passphrase.
The happy path is deliberately NOT tested here - it spends real credits, which
is the whole reason the script is run by hand.

Run in a subprocess. The script does sys.path surgery and reads config at
import, and it is not part of the application - importing it into the pytest
process would leak both.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import sys
import textwrap
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPT = BACKEND_DIR / "verify" / "verify_image_output.py"


def _run(passphrase: str | None, model: str = "") -> subprocess.CompletedProcess:
    """Drive the script's main() with the network poisoned.

    get_client is replaced by something that raises, so a test that somehow
    reached the transport fails loudly instead of quietly spending credits.

    getpass is replaced too, and not for convenience: on Windows it reads the
    CONSOLE directly rather than stdin, so feeding a passphrase through a pipe
    does nothing and the subprocess simply waits forever. Handing it a stub is
    the only way to drive this path unattended - which is also a fair warning
    that the real script is interactive by design.
    """
    getpass_stub = (
        "raise EOFError()" if passphrase is None
        else f"return {passphrase!r}"
    )
    code = textwrap.dedent(f"""
        import asyncio, getpass, runpy, sys
        sys.path.insert(0, {str(BACKEND_DIR)!r})

        def _fake_getpass(prompt=""):
            {getpass_stub}
        getpass.getpass = _fake_getpass

        import network_client
        def _no_network(*a, **kw):
            raise AssertionError("the script tried to reach the network")
        network_client.get_client = _no_network

        mod = runpy.run_path({str(SCRIPT)!r}, run_name="not_main")
        sys.exit(asyncio.run(mod["main"]()))
    """)
    env = dict(os.environ)
    env["ELYSIUM_IMAGE_MODEL"] = model
    # The child is a fresh interpreter: the suite's filesystem guard patches
    # attributes in THIS one and cannot see it at all. Without this line the
    # script resolves config.DATA_DIR to the real backend/ directory and opens
    # the developer's own salt.bin and verifier.bin - read-only on the path
    # these tests drive today, which is one identifier away from not being.
    # The exe test does the same thing for the same reason.
    with tempfile.TemporaryDirectory(prefix="elysium-imgverify-") as isolated:
        env["ELYSIUM_DATA_DIR"] = isolated
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=180, env=env,
        )


def test_without_a_model_it_spends_nothing_and_says_so():
    """The default state. It must no-op rather than guess a model."""
    out = _run(passphrase=None, model="")
    assert out.returncode == 2
    assert "No model chosen" in out.stdout


def test_a_wrong_passphrase_stops_before_the_network():
    """The vault refuses, and nothing is sent.

    get_client raises in this subprocess, so if the refusal ever stopped
    happening first, this test would fail on that instead of passing quietly.
    """
    out = _run(passphrase="definitely-not-the-passphrase",
               model="vendor/some-model")
    assert out.returncode == 1
    assert "wrong passphrase" in out.stdout
    assert "nothing was sent" in out.stdout
    assert "tried to reach the network" not in out.stderr


def test_no_passphrase_at_all_is_a_clean_refusal():
    """Closed stdin must not traceback, and must not proceed."""
    out = _run(passphrase=None, model="vendor/some-model")
    assert out.returncode == 1
    assert "no passphrase given" in out.stdout
    assert "Traceback" not in out.stderr
