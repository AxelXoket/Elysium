"""CSRF / cross-origin write shield (v1.1 Faz 0, FB1/H1/I4).

The middleware rejects cross-origin MUTATIONS while leaving safe methods and
same-origin/allowed-origin requests untouched. The dev frontend is genuinely
cross-origin (5173 -> 8787) and must keep working via FRONTEND_ORIGIN; the
packaged app is same-origin and must work via its own origin.
"""

import io

from PIL import Image

from config import FRONTEND_ORIGIN

# TestClient's default base_url is http://testserver, so the API's OWN origin
# (what the packaged same-origin app would send) is this:
OWN_ORIGIN = "http://testserver"
BAD_ORIGIN = "http://evil.example"


def _png_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(out, format="PNG")
    return out.getvalue()


# ── Allowed cases ────────────────────────────────────────────────────────────

def test_dev_frontend_origin_post_allowed(client):
    resp = client.post(
        "/api/v1/personas",
        json={"display_name": "Nova"},
        headers={"Origin": FRONTEND_ORIGIN},
    )
    assert resp.status_code == 201, resp.text


def test_packaged_same_origin_post_allowed(client):
    resp = client.post(
        "/api/v1/personas",
        json={"display_name": "Sol"},
        headers={"Origin": OWN_ORIGIN},
    )
    assert resp.status_code == 201, resp.text


def test_no_origin_no_metadata_allowed(client):
    # Non-browser tooling (the TestClient itself) sends neither header.
    resp = client.post("/api/v1/personas", json={"display_name": "Tau"})
    assert resp.status_code == 201, resp.text


def test_no_origin_same_origin_metadata_allowed(client):
    resp = client.post(
        "/api/v1/personas",
        json={"display_name": "Rhea"},
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert resp.status_code == 201, resp.text


def test_dev_origin_wins_over_same_site_metadata(client):
    # H1: the Origin branch takes precedence over Sec-Fetch-Site. Dev POSTs
    # carry Origin: FRONTEND_ORIGIN AND Sec-Fetch-Site: same-site (5173->8787
    # is cross-port = same-site); the shield must allow via the trusted Origin,
    # never reject on the metadata.
    resp = client.post(
        "/api/v1/personas",
        json={"display_name": "Vega"},
        headers={"Origin": FRONTEND_ORIGIN, "Sec-Fetch-Site": "same-site"},
    )
    assert resp.status_code == 201, resp.text


def test_get_never_blocked_even_with_bad_origin(client):
    resp = client.get("/api/v1/personas", headers={"Origin": BAD_ORIGIN})
    assert resp.status_code == 200, resp.text


def test_healthz_never_blocked(client):
    # Outside /api/v1 entirely.
    resp = client.get("/healthz", headers={"Origin": BAD_ORIGIN})
    assert resp.status_code == 200


# ── Denied cases ─────────────────────────────────────────────────────────────

def test_bad_origin_post_denied(client):
    resp = client.post(
        "/api/v1/personas",
        json={"display_name": "X"},
        headers={"Origin": BAD_ORIGIN},
    )
    assert resp.status_code == 403
    assert resp.json() == {"detail": "cross_origin_denied"}


def test_wrong_port_denied(client):
    # Same host, different port is NOT the allowed frontend origin.
    resp = client.post(
        "/api/v1/personas",
        json={"display_name": "X"},
        headers={"Origin": "http://127.0.0.1:9999"},
    )
    assert resp.status_code == 403


def test_origin_null_denied(client):
    resp = client.post(
        "/api/v1/personas",
        json={"display_name": "X"},
        headers={"Origin": "null"},
    )
    assert resp.status_code == 403


def test_bad_origin_bodyless_post_denied(client):
    # The exact reported vector: a body-less clear POST.
    char_id = _make_char(client)
    chat_id = _make_chat(client, char_id)
    resp = client.post(
        f"/api/v1/chats/{chat_id}/clear",
        headers={"Origin": BAD_ORIGIN},
    )
    assert resp.status_code == 403


def test_bad_origin_multipart_upload_denied(client):
    resp = client.post(
        "/api/v1/uploads/images",
        files={"file": ("x.png", _png_bytes(), "image/png")},
        headers={"Origin": BAD_ORIGIN},
    )
    assert resp.status_code == 403


def test_bad_origin_delete_denied(client):
    resp = client.delete(
        "/api/v1/personas/1",
        headers={"Origin": BAD_ORIGIN},
    )
    assert resp.status_code == 403


def test_bad_origin_unstage_delete_denied(client):
    # Regression: the new v1.1 unstage route inherits csrf_shield automatically
    # (method + /api/v1 prefix, not a route table). (I5.)
    resp = client.delete(
        "/api/v1/uploads/images/1",
        headers={"Origin": BAD_ORIGIN},
    )
    assert resp.status_code == 403
    assert resp.json() == {"detail": "cross_origin_denied"}


def test_no_origin_cross_site_metadata_denied(client):
    resp = client.post(
        "/api/v1/personas",
        json={"display_name": "X"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 403


def test_no_origin_same_site_metadata_denied(client):
    # same-site (cross-port) without an Origin header is treated as a forgery.
    resp = client.post(
        "/api/v1/personas",
        json={"display_name": "X"},
        headers={"Sec-Fetch-Site": "same-site"},
    )
    assert resp.status_code == 403


# ── I5: route-table sweep - every mutating /api/v1 route is behind the shield ─

def test_route_table_all_mutations_shielded(client):
    """Every /api/v1 route with a non-safe method rejects a bad Origin BEFORE
    the handler runs (the shield is middleware, not a per-route decorator).
    A path is checked with dummy params filled in - the 403 lands before
    routing, so a non-existent id never matters. (I5.)"""
    from main import app

    safe = {"GET", "HEAD", "OPTIONS"}
    checked = 0
    for route in app.router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or not path.startswith("/api/v1"):
            continue
        mutating = methods - safe
        if not mutating:
            continue
        # Fill path params with "1" so the URL is well-formed.
        url = path
        for part in path.split("/"):
            if part.startswith("{") and part.endswith("}"):
                url = url.replace(part, "1")
        for method in mutating:
            resp = client.request(
                method, url, headers={"Origin": BAD_ORIGIN},
            )
            assert resp.status_code == 403, (
                f"{method} {url} not shielded: {resp.status_code}"
            )
            assert resp.json() == {"detail": "cross_origin_denied"}
            checked += 1
    # Sanity: the sweep actually exercised routes (edit/upload-delete/etc.).
    assert checked >= 10


# ── helpers (local so this file is self-contained) ───────────────────────────

def _make_char(client) -> int:
    r = client.post("/api/v1/characters", json={"name": "C", "first_mes": "hi"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _make_chat(client, character_id: int) -> int:
    r = client.post("/api/v1/chats", json={"character_id": character_id})
    assert r.status_code == 201, r.text
    return r.json()["id"]
