"""The app shipped with no security response headers at all.

A repo-wide grep found zero matches for Content-Security-Policy,
X-Content-Type-Options, X-Frame-Options or Referrer-Policy. The only global
header was `Cache-Control: no-store` on /api/*.

The reason this matters here is not the usual one. There is no HTML sink in the
SPA - no innerHTML, no dangerouslySetInnerHTML, no markdown-to-HTML - so this is
not XSS mitigation. It is EXFILTRATION CONTAINMENT: the same origin serves the
whole unauthenticated vault API next to the renderer, so if anything ever did
execute there, `default-src 'self'` is what denies it a way out. And the image
route hands a browser a Content-Type straight out of a database column, which is
what `nosniff` plus a serve-time allowlist are for.

Four headers, deliberately, not a dozen - see main.py for what is left out and
why. Everything below is asserted as behaviour: which responses carry them,
including the ones that never reach a router.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

import main


def _headers(client, path: str):
    return client.get(path).headers


# ── the baseline, on ordinary responses ──────────────────────────────────────

@pytest.mark.parametrize("path", ["/healthz", "/api/v1/settings", "/api/v1/chats"])
def test_every_response_carries_the_baseline(client, path):
    h = _headers(client, path)
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert "default-src 'self'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"


def test_the_policy_keeps_the_two_things_the_app_actually_needs(client):
    """A policy that breaks the app gets reverted, and then protects nothing.

    blob: is required twice over (attachment previews before upload, and the
    chat wallpaper's CSS background-image) and inline styles are required by
    both the SPA and pywebview's own injected <style>.
    """
    csp = _headers(client, "/healthz")["content-security-policy"]
    assert "img-src 'self' blob:" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp


def test_the_policy_denies_the_exfiltration_routes(client):
    csp = _headers(client, "/healthz")["content-security-policy"]
    for directive in ("object-src 'none'", "frame-src 'none'",
                      "base-uri 'none'", "form-action 'none'"):
        assert directive in csp


def test_unsafe_eval_is_not_in_the_policy(client):
    """Zod probes `new Function("")`; the answer is 'jitless', never 'allow it'."""
    assert "unsafe-eval" not in _headers(client, "/healthz")["content-security-policy"]


# ── and on the responses that never reach a router ──────────────────────────

def test_a_locked_vault_423_is_covered():
    """vault_gate short-circuits without calling downstream, so this only holds
    while the header layer is the OUTERMOST one."""
    with TestClient(main.app) as c:              # a fresh app: vault locked
        r = c.get("/api/v1/chats")
        assert r.status_code == 423
        assert "content-security-policy" in r.headers
        assert r.headers["x-content-type-options"] == "nosniff"


def test_a_rejected_origin_403_is_covered():
    with TestClient(main.app) as c:
        r = c.post("/api/v1/chats", headers={"Origin": "https://evil.example"},
                   json={})
        assert r.status_code == 403
        assert "content-security-policy" in r.headers


def test_a_rejected_host_400_is_covered():
    with TestClient(main.app) as c:
        r = c.get("/healthz", headers={"Host": "evil.example"})
        assert r.status_code == 400
        assert "content-security-policy" in r.headers


# ── setdefault, not assignment ───────────────────────────────────────────────

def test_a_route_that_chose_its_own_cache_policy_keeps_it(client):
    """The new layer must not trample a deliberate per-route decision, which is
    the same rule no_store_api already follows."""
    h = _headers(client, "/api/v1/settings")
    assert h["cache-control"] == "no-store"
    assert "content-security-policy" in h


# ── CORP: packaged only, on purpose ─────────────────────────────────────────

def test_no_corp_header_in_a_dev_build(client):
    """In dev the SPA is on another port, and Chromium treats same-site exactly
    like same-origin across ports on an IP host - so any value strict enough to
    help would break dev thumbnails and silently kill the voice preview."""
    assert "cross-origin-resource-policy" not in _headers(client, "/healthz")


def test_the_packaged_build_closes_the_no_cors_read(client, monkeypatch):
    """csrf_shield exempts GET by design and TrustedHost allows 127.0.0.1, so a
    remote page that guesses the port can <img src> a private attachment. It
    cannot read the pixels, but it learns the file exists and how big it is."""
    monkeypatch.setattr(main, "_CORP", "same-origin")
    assert _headers(client, "/healthz")["cross-origin-resource-policy"] == (
        "same-origin"
    )


# ── the image route, where the Content-Type comes from the database ──────────

def _png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _upload(client) -> int:
    resp = client.post("/api/v1/uploads/images",
                       files={"file": ("a.png", _png_bytes(), "image/png")})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_a_served_image_carries_nosniff_and_no_store(client):
    att = _upload(client)
    r = client.get(f"/api/v1/uploads/images/{att}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


def test_a_row_whose_mime_is_not_an_image_is_refused(client):
    """Defence in depth on the one value in that response a browser acts on.
    save_upload derives the mime from the bytes Pillow decoded, so today nothing
    can write this - but the URL is same-origin with the SPA and the whole local
    API, so it must not be one careless writer away from a scripted document."""
    import database

    att = _upload(client)
    with database.get_db() as con:
        con.execute("UPDATE attachments SET mime = ? WHERE id = ?",
                    ("image/svg+xml", att))

    r = client.get(f"/api/v1/uploads/images/{att}")
    assert r.status_code == 415
    assert r.json()["detail"] == "unsupported_media_type"
    assert b"svg" not in r.content


def test_the_refusal_does_not_leak_the_bytes(client):
    import database

    att = _upload(client)
    with database.get_db() as con:
        con.execute("UPDATE attachments SET mime = ? WHERE id = ?",
                    ("text/html", att))
    r = client.get(f"/api/v1/uploads/images/{att}")
    assert r.status_code == 415
    assert r.headers["content-type"].startswith("application/json")
