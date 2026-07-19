"""mock_provider.py - Minimal local OpenRouter stand-in for E2E smoke tests.

Run:    python tests/mock_provider.py [port]        (default 9797)
Then:   set OPENROUTER_BASE_URL=http://127.0.0.1:9797/api/v1  and start the
        backend; the full app works end-to-end with zero network egress and
        zero credits spent.

Serves:
    GET  /api/v1/models            - two fake models with realistic metadata
    GET  /api/v1/models/user       - same list (source="user")
    GET  /api/v1/key               - 200 (any key validates)
    POST /api/v1/chat/completions  - non-stream JSON or SSE stream depending
                                     on the request body's "stream" flag.
                                     Streams word-by-word with keepalive
                                     comments and a [DONE] terminator,
                                     mirroring real OpenRouter framing.

Stdlib only - no dependencies.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = [
    {
        "id": "mock/rp-model-large",
        "name": "Mock RP Large",
        "description": "Local mock model for smoke tests.",
        "context_length": 32768,
        "architecture": {
            "input_modalities": ["text", "image"],  # vision-capable for E2E
            "output_modalities": ["text"],
        },
        "top_provider": {"context_length": 32768, "max_completion_tokens": 4096},
        "supported_parameters": [
            "temperature", "top_p", "top_k", "min_p", "max_tokens",
            "frequency_penalty", "presence_penalty", "repetition_penalty",
            "seed", "stop",
        ],
        "pricing": {"prompt": "0", "completion": "0"},
        "created": 1720000000,
        "canonical_slug": "mock/rp-model-large",
    },
    {
        "id": "mock/rp-model-small",
        "name": "Mock RP Small",
        "description": "Smaller local mock model.",
        "context_length": 8192,
        "architecture": {
            "input_modalities": ["text"],
            "output_modalities": ["text"],
        },
        "top_provider": {"context_length": 8192, "max_completion_tokens": 2048},
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "pricing": {"prompt": "0", "completion": "0"},
        "created": 1720000001,
        "canonical_slug": "mock/rp-model-small",
    },
]

REPLY_WORDS = (
    "Elbette! Bu, yerel mock saglayicidan kelime kelime akan bir test "
    "yanitidir. Streaming, iptal ve kalicilik davranislarini dogrulamak "
    "icin yeterince uzun birkac cumle iceriyor. Her sey yolunda gorunuyor."
).split(" ")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[mock-openrouter] %s\n" % (fmt % args))

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/v1/models"):
            self._json({"data": MODELS})
        elif self.path.startswith("/api/v1/key"):
            self._json({"data": {"label": "mock", "usage": 0}})
        else:
            self._json({"error": "not_found"}, 404)

    def do_POST(self):
        if not self.path.startswith("/api/v1/chat/completions"):
            self._json({"error": "not_found"}, 404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "bad_json"}, 400)
            return

        model = payload.get("model", "mock/rp-model-large")
        full_text = " ".join(REPLY_WORDS)

        # Log which generation params arrived (keys + stop value) so E2E smoke
        # runs can assert parameter passthrough without reading chat content.
        gen_keys = sorted(
            k for k in payload.keys()
            if k not in ("model", "messages", "provider", "stream")
        )
        # Count image parts across multimodal content arrays (never log data).
        image_count = 0
        for msg in payload.get("messages", []):
            content = msg.get("content")
            if isinstance(content, list):
                image_count += sum(
                    1 for p in content
                    if isinstance(p, dict) and p.get("type") == "image_url"
                )
        sys.stderr.write(
            "[mock-openrouter] completion params: %s%s images=%d\n" % (
                gen_keys,
                " stop=%r" % (payload.get("stop"),) if "stop" in payload else "",
                image_count,
            )
        )

        if not payload.get("stream"):
            self._json({
                "id": "gen-mock-1",
                "object": "chat.completion",
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": full_text},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 42, "completion_tokens": 37},
            })
            return

        # SSE stream, mirroring OpenRouter framing.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(b": OPENROUTER PROCESSING\n\n")
            self.wfile.flush()
            for i, word in enumerate(REPLY_WORDS):
                chunk = {
                    "id": "gen-mock-1",
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": word + (" " if i < len(REPLY_WORDS) - 1 else "")},
                        "finish_reason": None,
                    }],
                }
                self.wfile.write(
                    b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n"
                )
                self.wfile.flush()
                time.sleep(0.06)  # visible word-by-word pacing
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (ConnectionAbortedError, BrokenPipeError):
            sys.stderr.write("[mock-openrouter] client aborted stream\n")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9797
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    sys.stderr.write(f"[mock-openrouter] listening on 127.0.0.1:{port}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
