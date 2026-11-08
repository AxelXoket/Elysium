"""My blind calibration (F3, V1): 20 LAYER-vs-OFF pairs, labelled blind.

The page is a plain HTML file served on 127.0.0.1 only. It carries the
story and two replies per pair, labelled A and B at random; it never carries
a model name, an arm name or a call id. Which side was which lives in
calibration_key.json in the output directory, which the page never sees.

Nothing here opens a browser: the URL is printed and I open it.
Writes are accepted only with the per-session token in the URL and only from
a same-origin page on the loopback host (no other site can post labels).
"""

from __future__ import annotations

import json
import os
import random
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import EVAL_DIR, config, fixtures
from .judge import _card_view, _contexts_by_id, _story, final_replies, pair_specs

N_PAIRS = 20
PAGE_TEMPLATE = EVAL_DIR / "calibration.html"
LABELS_FILE = "owner_labels.json"
KEY_FILE = "calibration_key.json"
VALID = ("A", "B", "tie")


def select_pairs(replies: dict[str, dict], n: int = N_PAIRS,
                 seed: int = config.SEED) -> list[dict]:
    """n quality pairs, round-robin over models, seeded; sides randomised."""
    rng = random.Random(seed)
    by_model: dict[str, list[dict]] = {}
    for s in pair_specs():
        if s["kind"] == "quality" and s["first"] in replies and s["second"] in replies:
            by_model.setdefault(s["model"], []).append(s)
    for v in by_model.values():
        rng.shuffle(v)
    chosen: list[dict] = []
    while len(chosen) < n and any(by_model.values()):
        for m in sorted(by_model):
            if by_model[m] and len(chosen) < n:
                chosen.append(by_model[m].pop())
    rng.shuffle(chosen)
    out = []
    for i, s in enumerate(chosen, 1):
        layer_is_a = rng.random() < 0.5
        out.append({"pid": f"p{i:02d}", "ctx": s["ctx"], "model": s["model"],
                    "A": s["first"] if layer_is_a else s["second"],
                    "B": s["second"] if layer_is_a else s["first"],
                    "layer_side": "A" if layer_is_a else "B"})
    return out


def page_items(pairs: list[dict], replies: dict[str, dict]) -> list[dict]:
    """What the page may show: no model, no arm, no call id."""
    ctxs = _contexts_by_id()
    user = fixtures.persona()["display_name"]
    items = []
    for p in pairs:
        ctx = ctxs[p["ctx"]]
        items.append({"pid": p["pid"], "card": _card_view(ctx.card, user),
                      "story": _story(ctx.history, ctx.user, user, ctx.card["name"]),
                      "A": replies[p["A"]]["text"], "B": replies[p["B"]]["text"]})
    return items


def render_page(items: list[dict], token: str, nonce: str) -> str:
    data = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    html = PAGE_TEMPLATE.read_text(encoding="utf-8")
    return (html.replace("__PAIRS__", data).replace("__TOKEN__", token)
            .replace("__NONCE__", nonce))


def prepare(out_dir: Path) -> tuple[list[dict], list[dict]]:
    """Choose the pairs once and keep the choice: reruns show the same page."""
    from .runner import read_ledger

    replies = final_replies(read_ledger(out_dir))
    key_path = out_dir / KEY_FILE
    if key_path.exists():
        pairs = json.loads(key_path.read_text(encoding="utf-8"))
    else:
        pairs = select_pairs(replies)
        key_path.write_text(json.dumps(pairs, indent=1), encoding="utf-8")
    return pairs, page_items(pairs, replies)


def clean_labels(raw, pids: set[str]) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("labels must be an object")
    out = {}
    for k, v in raw.items():
        if k not in pids or v not in VALID:
            raise ValueError("unknown pair or label")
        out[k] = v
    return out


def make_server(out_dir: Path, port: int = 0):
    pairs, items = prepare(out_dir)
    pids = {p["pid"] for p in pairs}
    token = secrets.token_urlsafe(18)
    labels_path = out_dir / LABELS_FILE

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # no request log: the URL carries the token
            pass

        def _origin_ok(self) -> bool:
            host = f"127.0.0.1:{self.server.server_address[1]}"
            if self.headers.get("Host") != host:
                return False
            origin = self.headers.get("Origin")
            return origin in (None, f"http://{host}")

        def _token_ok(self) -> bool:
            q = parse_qs(urlparse(self.path).query)
            return secrets.compare_digest((q.get("t") or [""])[0], token)

        def _send(self, code: int, body: bytes, ctype: str, extra=None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if not (self._origin_ok() and self._token_ok()):
                return self._send(403, b"forbidden", "text/plain")
            path = urlparse(self.path).path
            if path == "/":
                nonce = secrets.token_urlsafe(12)
                csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; "
                       "style-src 'unsafe-inline'; connect-src 'self'")
                page = ("<!doctype html><meta charset=\"utf-8\">"
                        + render_page(items, token, nonce))
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": csp})
            if path == "/labels":
                data = labels_path.read_bytes() if labels_path.exists() else b"{}"
                return self._send(200, data, "application/json")
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            if not (self._origin_ok() and self._token_ok()):
                return self._send(403, b"forbidden", "text/plain")
            if urlparse(self.path).path != "/labels":
                return self._send(404, b"not found", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64 * 1024:
                return self._send(413, b"too large", "text/plain")
            try:
                labels = clean_labels(json.loads(self.rfile.read(n) or b"{}"), pids)
            except ValueError:
                return self._send(400, b"bad labels", "text/plain")
            tmp = labels_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(labels, indent=1), encoding="utf-8")
            os.replace(tmp, labels_path)
            return self._send(204, b"", "text/plain")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={token}"
    return server, url
