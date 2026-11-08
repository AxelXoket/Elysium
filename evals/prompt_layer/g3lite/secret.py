"""The OpenRouter key: read from one environment variable, never shown.

Rules:
  * the key comes ONLY from config.KEY_ENV; there is no flag, file or prompt;
  * it is never printed, logged or written. Every line this harness writes
    (stdout, stderr, status file, ledger, reports) passes through a Redactor
    first, and SafeWriter refuses to write a line that still carries it;
  * the judge's child process never inherits it (judge.judge_env).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from . import config

REDACTED = "[redacted]"
#: Any OpenRouter-shaped key, not only ours: a provider error that echoes a
#: different key must not be written either.
_OR_KEY_SHAPE = re.compile(r"sk-or-[A-Za-z0-9_\-]{8,}")


class MissingKey(RuntimeError):
    pass


class KeyLeak(RuntimeError):
    """A write would have carried the key. Raised instead of writing."""


def read_key(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    key = (env.get(config.KEY_ENV) or "").strip()
    if not key:
        raise MissingKey(f"set {config.KEY_ENV} in this shell (the value is never shown)")
    return key


class Redactor:
    def __init__(self, *secrets: str) -> None:
        self._secrets = [s for s in secrets if s]

    def __call__(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, REDACTED)
        return _OR_KEY_SHAPE.sub(REDACTED, text)

    def assert_clean(self, text: str) -> None:
        for s in self._secrets:
            if s in text:
                raise KeyLeak("refused to write a line carrying the key")


class SafeWriter:
    """Append-only text sink that redacts, then verifies, every write."""

    def __init__(self, path, redact: Redactor) -> None:
        self.path = path
        self.redact = redact

    def write(self, text: str) -> None:
        clean = self.redact(text)
        self.redact.assert_clean(clean)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(clean)


def safe_print(redact: Redactor, text: str, *, stream=None) -> None:
    import sys
    clean = redact(text)
    redact.assert_clean(clean)
    print(clean, file=stream or sys.stdout, flush=True)
