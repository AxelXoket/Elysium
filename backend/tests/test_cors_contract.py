"""A headline security promise that nothing running was checking.

README lists "Strict CORS + Host allowlist" among the guarantees. The only
thing verifying it was `verify/verify_phase5b.py`, which has been crashing
before it reaches its CORS section - and even if it ran, one grep in the
pytest suite (test_verify_gate) only asserts the STRING `allow_origins=["*"]`
is absent from main.py. A string that is absent proves nothing about the
headers a browser actually receives: the middleware could be registered in the
wrong order, wrapped by something that strips its headers, or handed an origin
list that quietly includes everything.

So these drive the real middleware and read the real response headers.

Why it matters here specifically: this API has no authentication. Loopback is
not a permission boundary, and the launch token defends against other
PROCESSES; CORS is what stands between a web page the user happens to have
open and every chat in the vault. `allow_credentials=False` plus a closed
origin list is that wall.
"""
from __future__ import annotations

import pytest

from config import FRONTEND_ORIGIN

#: A page that is not the app. The check must not be "does it contain
#: localhost" - a hostile origin can say localhost.evil.example and pass a
#: substring test while being a completely different site to a browser.
EVIL = "http://localhost.evil.example"

PREFLIGHT = {
    "Origin": FRONTEND_ORIGIN,
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "Content-Type",
}


def test_the_app_origin_is_allowed(client):
    resp = client.options("/api/v1/settings", headers=PREFLIGHT)

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == FRONTEND_ORIGIN


def test_the_allowed_origin_is_named_never_a_wildcard(client):
    """A wildcard would let any page on the internet read every reply."""
    resp = client.options("/api/v1/settings", headers=PREFLIGHT)

    assert resp.headers["access-control-allow-origin"] != "*"


@pytest.mark.parametrize("origin", [
    EVIL,
    "https://evil.example",
    "http://127.0.0.1:5174",          # right host, wrong port
    "http://localhost:5173",          # right port, different host spelling
    "null",                           # a sandboxed iframe or a file:// page
])
def test_a_foreign_origin_is_not_told_it_may_read_this(client, origin: str):
    """The refusal that matters is the ABSENCE of the header. A browser only
    hands the body to the page when the server names its origin back."""
    resp = client.options("/api/v1/settings", headers={
        **PREFLIGHT, "Origin": origin,
    })

    allowed = resp.headers.get("access-control-allow-origin")
    assert allowed != origin
    assert allowed != "*"


def test_a_foreign_origin_is_refused_on_a_real_request_too(client):
    """Not just on the preflight. A simple GET needs no preflight at all, so a
    middleware that only guarded OPTIONS would guard nothing."""
    resp = client.get("/api/v1/settings", headers={"Origin": EVIL})

    assert resp.headers.get("access-control-allow-origin") != EVIL


def test_credentials_are_never_allowed(client):
    """allow_credentials=True is what would let a page send the user's cookies
    and read the answer. This app authenticates nothing by cookie, so the flag
    would be pure attack surface."""
    resp = client.options("/api/v1/settings", headers=PREFLIGHT)

    assert resp.headers.get("access-control-allow-credentials") != "true"


def test_only_the_methods_the_app_uses_are_offered(client):
    resp = client.options("/api/v1/settings", headers=PREFLIGHT)

    offered = {
        m.strip().upper()
        for m in resp.headers.get("access-control-allow-methods", "").split(",")
        if m.strip()
    }
    assert offered == {"GET", "POST", "DELETE", "PATCH"}
    # PUT and OPTIONS are absent on purpose; a wildcard would be worse still.
    assert "*" not in offered


def test_authorization_is_not_an_allowed_request_header(client):
    """The provider key travels from the vault to OpenRouter and never through
    a browser. A page allowed to set Authorization here could try to make this
    API forward one of its own."""
    resp = client.options("/api/v1/settings", headers={
        **PREFLIGHT, "Access-Control-Request-Headers": "Authorization",
    })

    allowed = resp.headers.get("access-control-allow-headers", "")
    assert "authorization" not in allowed.lower()
    assert "*" not in allowed


# ---------------------------------------------------------------------------
# The other half of the pair: the Host allowlist
# ---------------------------------------------------------------------------

def test_a_foreign_host_header_is_refused(client):
    """CORS cannot stop DNS rebinding: a hostile domain that re-resolves to
    127.0.0.1 is SAME-origin to the browser, so no CORS header is consulted.
    The Host allowlist is what closes that, and it is half of the README's
    promise - the half nothing was checking."""
    resp = client.get("/api/v1/settings", headers={"Host": "evil.example"})

    assert resp.status_code == 400


def test_the_hosts_the_app_really_uses_still_work(client):
    """Guard the guard: an allowlist that refused everything would 'pass' every
    test above while breaking the app."""
    for host in ("127.0.0.1", "localhost", "testserver"):
        resp = client.get("/healthz", headers={"Host": host})
        assert resp.status_code == 200, host
