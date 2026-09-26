"""W1 round 2, L1 and L2: camelCase secret keys and the upload's file name.

L1: `strip_secrets` matched `token` only as a `_ - .` separated segment, so
`accessToken`, `authToken` and `extensions.x.refreshToken` survived into the
stored preset. Keys are now also split at camelCase boundaries and matched on
a `token` / `key` ending, case-insensitively.

L2: the file name header had no length cap and kept control characters; a
5000-character name was stored and `%00.txt` made a module named NUL. The
decoded name is now capped at FILENAME_MAX_CHARS and any control character is
refused with a named error, before the body is read.
"""
from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from prompt_modules import errors as E
from prompt_modules import parsers as P
from prompt_modules.limits import FILENAME_HEADER, FILENAME_MAX_CHARS

BASE = "/api/v1/prompt-modules"

#: Each value is unique so a hit in the stored text names the key that leaked.
CAMEL_SECRETS = {
    "accessToken": "camel-secret-1",
    "authToken": "camel-secret-2",
    "refreshToken": "camel-secret-3",
    "apiKey": "camel-secret-4",
    "ACCESSTOKEN": "camel-secret-5",
    "idToken": "camel-secret-6",
    "clientKey": "camel-secret-7",
    "sessionId": "camel-secret-8",
}
#: Ground: keys that only look like the words above.
LOOKALIKES = {
    "openai_max_tokens": 300,
    "maxTokens": 200,
    "tokenizer": "cl100k",
    "keepAlive": True,
    "tokensPerSecond": 9,
}


def _doc() -> dict:
    return {
        **LOOKALIKES,
        **CAMEL_SECRETS,
        "extensions": {"x": {"refreshToken": "camel-secret-9"}},
        "prompts": [{"identifier": "a", "name": "A", "content": "Alpha.",
                     "authToken": "camel-secret-10"}],
    }


def _stored(result) -> str:
    return (json.dumps(result.preset_json) + json.dumps(result.modules[0].raw_entry)
            + json.dumps(result.report))


class TestCamelCaseSecretKeys:
    def test_camel_case_and_upper_case_secret_keys_never_reach_storage(self):
        r = P.parse_upload("p.json", json.dumps(_doc()).encode())
        stored = _stored(r)
        for n in range(1, 11):
            assert f"camel-secret-{n}" not in stored, n
        removed = set(r.report["secrets_removed"])
        assert {f"$.{k}" for k in CAMEL_SECRETS} <= removed
        assert "$.extensions.x.refreshToken" in removed
        assert "$.prompts[0].authToken" in removed

    def test_ground_lookalike_keys_and_prompt_text_are_kept(self):
        r = P.parse_upload("p.json", json.dumps(_doc()).encode())
        for key, value in LOOKALIKES.items():
            assert r.preset_json[key] == value, key
        assert r.modules[0].content == "Alpha."

    @pytest.mark.parametrize("key", list(CAMEL_SECRETS))
    def test_each_key_is_a_secret_on_its_own(self, key):
        # Positive control per key, on the function the walk uses.
        assert P._is_secret_key(key)

    @pytest.mark.parametrize("key", list(LOOKALIKES))
    def test_ground_each_lookalike_is_not(self, key):
        assert not P._is_secret_key(key)


def _upload(client, filename: str, data: bytes = b"hello"):
    return client.post(f"{BASE}/import", content=data,
                       headers={FILENAME_HEADER: quote(filename),
                                "Content-Type": "application/octet-stream"})


class TestTheFileName:
    def test_ground_an_ordinary_name_at_the_cap_is_accepted(self, client):
        name = "a" * (FILENAME_MAX_CHARS - 4) + ".txt"
        assert len(name) == FILENAME_MAX_CHARS
        resp = _upload(client, name)
        assert resp.status_code == 201, resp.text

    def test_a_name_over_the_cap_is_refused_by_name(self, client):
        name = "a" * (FILENAME_MAX_CHARS - 3) + ".txt"
        resp = _upload(client, name)
        assert resp.status_code == 400
        assert resp.json()["detail"] == E.FILENAME_INVALID
        assert resp.json()["max_chars"] == FILENAME_MAX_CHARS
        assert name not in resp.text

    @pytest.mark.parametrize("name", ["\x00.txt", "a\x00b.txt", "a\x07.md",
                                      "a\x1bname.json", "a\x7f.txt", "a\x85.txt",
                                      "line\nbreak.txt"])
    def test_a_control_character_is_refused_by_name(self, client, name):
        resp = _upload(client, name)
        assert resp.status_code == 400
        assert resp.json()["detail"] == E.FILENAME_INVALID
        listed = client.get(BASE).json()["modules"]
        assert listed == []

    def test_ground_unicode_letters_and_spaces_are_a_normal_name(self, client):
        resp = _upload(client, "kisa sahne çş.md")
        assert resp.status_code == 201, resp.text

    def test_the_refusal_comes_before_the_body_is_read(self, client, monkeypatch):
        from starlette.requests import Request
        pulled = []
        real = Request.stream

        async def spy(self):
            async for chunk in real(self):
                pulled.append(len(chunk))
                yield chunk
        monkeypatch.setattr(Request, "stream", spy)
        resp = _upload(client, "a\x00.txt", b"x" * 1000)
        assert resp.json()["detail"] == E.FILENAME_INVALID
        assert sum(pulled) == 0
        # Positive control: the spy does see a body that is read.
        resp = _upload(client, "ok.txt", b"x" * 1000)
        assert resp.status_code == 201, resp.text
        assert sum(pulled) >= 1000

    def test_the_parser_refuses_it_too(self):
        # The parser is the second door (the route is the first).
        with pytest.raises(E.PromptModuleError) as err:
            P.parse_upload("a\x00.txt", b"hello")
        assert err.value.code == E.FILENAME_INVALID
        assert P.parse_upload("a.txt", b"hello").modules
