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
  * in the URL FRAGMENT, which is never sent to a server and never written to
    a request log, read once by the page at boot and kept in memory only.
    This is what Jupyter does, for the same reason.

The part of that which is NOT a guarantee, said rather than implied: the page
strips the fragment on its first line, but the browser engine has its own
session-restore machinery underneath, and it can write the initial navigation
URL to disk before any of our JavaScript runs. browser_profile.purge() removes
those files ("Sessions", "EdgeSessions", "EdgeJourneys") at launch and at a
graceful exit - so a hard kill can leave one on disk until the next launch.
The token is per-launch and useless once the process is gone, which is what
makes this an acceptable gap rather than a hole, but it is a gap.

The token is also stripped from every subprocess this app spawns. The voice
worker and the installer both run code this project did not write, and
`dict(os.environ)` would otherwise hand them exactly the credential the gate
exists to withhold from other processes.

Absent by default. A developer running uvicorn by hand has no token, and
requiring one would break that with an error nobody could interpret - so the
gate is armed only when run_app.py generates one, which is the packaged app.
"""
from __future__ import annotations

import hmac
import os
import secrets

#: How run_app hands the token to the server it starts in-process.
ENV_VAR = "ELYSIUM_LAUNCH_TOKEN"

#: The header the frontend sends it back in. A custom header cannot be set by
#: a cross-origin form or an <img>, so it is also a second CSRF defence.
HEADER = "X-Elysium-Token"

_token: str | None = None


def issue() -> str:
    """Make this launch's token and publish it to the process environment."""
    global _token
    _token = secrets.token_urlsafe(32)
    os.environ[ENV_VAR] = _token
    return _token


def configured() -> str | None:
    """The token this process expects, or None when the gate is not armed."""
    if _token is not None:
        return _token
    value = os.environ.get(ENV_VAR) or ""
    return value or None


def reset() -> None:
    """Test seam. Never called by the app."""
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
    return hmac.compare_digest(presented, expected)
