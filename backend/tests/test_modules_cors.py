"""Dev preflight (5173 -> 8787) for the prompt module routes."""

from __future__ import annotations

from config import FRONTEND_ORIGIN
from prompt_modules.limits import FILENAME_HEADER


def _preflight(client, method: str, headers: str = "Content-Type"):
    return client.options("/api/v1/prompt-modules/settings", headers={
        "Origin": FRONTEND_ORIGIN,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": headers,
    })


def test_get_preflight_still_allowed(client):
    """Ground: nothing that worked before stopped working."""
    r = _preflight(client, "GET")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == FRONTEND_ORIGIN


def test_put_preflight_is_allowed(client):
    r = _preflight(client, "PUT")
    assert r.status_code == 200
    assert "PUT" in r.headers["access-control-allow-methods"]


def test_the_filename_header_may_be_sent(client):
    r = _preflight(client, "POST", f"Content-Type, {FILENAME_HEADER}")
    assert r.status_code == 200
    assert FILENAME_HEADER.lower() in r.headers["access-control-allow-headers"].lower()


def test_an_unlisted_header_is_still_refused(client):
    """Positive control that the preflight can say no."""
    r = _preflight(client, "POST", "X-Something-Else")
    assert r.status_code == 400


def test_no_response_header_is_exposed(client):
    """The H4 refusal carries its module list in the JSON body, so the app
    exposes no response header to the page. Positive control: the same
    response IS a CORS response (the origin is echoed), so an absent expose
    header is not just an absent CORS layer."""
    r = client.get("/api/v1/prompt-modules/settings", headers={"Origin": FRONTEND_ORIGIN})
    assert r.headers.get("access-control-allow-origin") == FRONTEND_ORIGIN
    assert "access-control-expose-headers" not in r.headers
