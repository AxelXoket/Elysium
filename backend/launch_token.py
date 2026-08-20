"""A secret only this launch's window knows, so only it can use the API.

Everything guarding the local server so far guards it against a BROWSER:
TrustedHostMiddleware kills DNS rebinding, the CSRF shield refuses a foreign
Origin, CORS is narrow. All of that assumes the attacker is a web page, which
is the right assumption for the web and the wrong one here.

Nothing stopped a PROCESS. Any program running as this user - a script, a
sync client, something that arrived in a download - can `curl
http://127.0.0.1:<port>/api/v1/chats` and read every conversation, because
loopback is not a permission boundary and none of the above is a check on WHO
is asking. The vault is irrelevant to this: while the app is open it is
unlocked by definition, which is exactly when the data is worth taking.

So the window is given a secret at launch and every API request must carry it.

Where the secret is NOT put, and why:

  * not in the query string - it would land in access logs, in the referrer
    of any resource the page loads, and in the browser's own history store;
  * not in localStorage - readable by anything that can reach the profile
    directory on disk, which outlives the session the token belongs to;
  * NOT IN THIS PROCESS'S ENVIRONMENT, which is the one that got away for a
    while. issue() used to do `os.environ[ENV_VAR] = _token`, and a process
    environment is not private: an unprivileged program running as the same
    user opens this process with QUERY_INFORMATION|VM_READ, walks the PEB to
    ProcessParameters.Environment and reads the block back verbatim. That was
    demonstrated against this module with a working exploit, handle opened
    with error 0. Every word above about logs and profile directories was
    reasoning about the browser while the secret sat in the one place the
    stated adversary - "any program running as this user" - reads for free.
    The environment block is also inherited by every child, so each one was a
    second copy we then had to remember to scrub.
  * in the URL FRAGMENT, which is never sent to a server and never written to
    a request log, read once by the page at boot and kept in memory only.
    This is what Jupyter does, for the same reason.

The token therefore lives in exactly one place: the module global below, in
the memory of the process that issued it. Nothing has to carry it anywhere,
because uvicorn runs on a THREAD of that same process (run_app.serve), so the
gate reads the same global the launcher wrote.

The part of that which is NOT a guarantee, said rather than implied: the page
strips the fragment on its first line, but the browser engine has its own
session-restore machinery underneath, and it can write the initial navigation
URL to disk before any of our JavaScript runs. browser_profile.purge() removes
those files ("Sessions", "EdgeSessions", "EdgeJourneys") at launch and at a
graceful exit - so a hard kill can leave one on disk until the next launch.
The token is per-launch and useless once the process is gone, which is what
makes this an acceptable gap rather than a hole, but it is a gap.

The voice worker and the installer additionally strip ENV_VAR by name from
the environment they build for their children (tts/worker_client.py,
tts/provision.py). Those strips are now belt and braces, not the defence:
nothing in this app puts the token in an environment for them to inherit, and
they would keep a stray value out only if some future change put one back.
The defence is that the secret never leaves this process's memory.

Absent by default. A developer running uvicorn by hand has no token, and
requiring one would break that with an error nobody could interpret - so the
gate is armed only when run_app.py generates one, which is the packaged app.
That developer can still arm it deliberately by setting ENV_VAR in their own
shell; unset, as it is by default, the gate stays open exactly as before.
"""
from __future__ import annotations

import hmac
import os
import secrets

#: Never written by this app. Two things still need the name: a developer who
#: wants to arm the gate against a hand-run uvicorn (see configured), and the
#: subprocess strip lists that keep a stray ambient value out of a child.
ENV_VAR = "ELYSIUM_LAUNCH_TOKEN"

#: The header the frontend sends it back in. A custom header cannot be set by
#: a cross-origin form or an <img>, so it is also a second CSRF defence.
HEADER = "X-Elysium-Token"

_token: str | None = None


def issue() -> str:
    """Make this launch's token. In memory only, deliberately.

    It is NOT written to os.environ. That is the whole point of this function
    being three lines: the server that checks the token runs on a thread of
    this same process (run_app.serve), so a module global reaches it, while an
    environment variable would also reach every other program running as this
    user - see the module docstring for the exploit that proved it.
    """
    global _token
    _token = secrets.token_urlsafe(32)
    return _token


def configured() -> str | None:
    """The token this process expects, or None when the gate is not armed.

    The env fallback is a DEVELOPER seam and nothing else. The app never
    writes ENV_VAR, so in the packaged product this branch is only ever
    reached when issue() has not run - a hand-started `uvicorn main:app`,
    where it lets someone arm the gate on purpose to exercise it. Reading it
    is safe in a way writing it is not: another process cannot put a value
    into this process's environment, and if one somehow could, _token takes
    precedence whenever a real launch has issued one.
    """
    if _token is not None:
        return _token
    value = os.environ.get(ENV_VAR) or ""
    return value or None


def reset() -> None:
    """Test seam. Never called by the app.

    Still pops ENV_VAR even though issue() no longer sets it: a test that
    exercises the developer seam above sets the variable itself, and reset has
    to undo that too or the next test inherits an armed gate.
    """
    global _token
    _token = None
    os.environ.pop(ENV_VAR, None)


def accepts(presented: str | None) -> bool:
    """Whether this request may proceed.

    True when no token is configured: an unarmed gate has to be open, or every
    development run and every existing test fails with a 403 that describes a
    misconfiguration rather than an attack.

    compare_digest, not ==, because the comparison is against a secret and the
    caller controls the input. The timing signal from an ordinary string
    compare is small over loopback and it is free to not have it.
    """
    expected = configured()
    if expected is None:
        return True
    if not presented:
        return False
    # Encoded, not compared as text. Starlette decodes header bytes as
    # latin-1, so any byte over 0x7f arrives as a non-ASCII str and
    # compare_digest raises TypeError on it - which escaped the gate and
    # became a 500 with a traceback, triggerable by exactly the local process
    # this exists to refuse. A gate that answers "server error" to a hostile
    # input has not refused it. Measured at the ASGI layer; the test client
    # cannot send the byte, which is why no test saw it.
    return hmac.compare_digest(presented.encode("utf-8", "surrogateescape"),
                               expected.encode("utf-8", "surrogateescape"))
