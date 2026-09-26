"""Prompt modules over HTTP: upload path, list, toggles, order, settings.

MT-6  byte caps before the body is read, exact caps accepted
MT-9  an upload writes nothing to disk and never takes the multipart path
H3    everything starts off; "enable what SillyTavern had on" is all or none
"""

from __future__ import annotations

import builtins
import io
import json
import os
import tempfile
from urllib.parse import quote

import pytest

import database
from prompt_modules import errors as E
from prompt_modules import store
from prompt_modules.limits import (
    FILENAME_HEADER,
    JSON_MAX_BYTES,
    MODULE_MAX_CHARS,
    TXT_MD_MAX_BYTES,
)

BASE = "/api/v1/prompt-modules"


def upload(client, filename, data, content_type="application/octet-stream", **kw):
    headers = {FILENAME_HEADER: quote(filename), "Content-Type": content_type}
    headers.update(kw.pop("headers", {}))
    return client.post(f"{BASE}/import", content=data, headers=headers, **kw)


def modules(client) -> list[dict]:
    r = client.get(BASE)
    assert r.status_code == 200, r.text
    return r.json()["modules"]


# ---------------------------------------------------------------------------
# MT-6: byte caps, before reading
# ---------------------------------------------------------------------------

@pytest.fixture()
def stream_spy(monkeypatch):
    """Counts every body byte the app pulls from the connection."""
    from starlette.requests import Request

    pulled = {"calls": 0, "bytes": 0, "depth": 0}
    real = Request.stream

    async def spy(self):
        # Each http middleware wraps the request and streams through the one
        # inside it, so only the outermost call counts bytes.
        pulled["calls"] += 1
        outer = pulled["depth"] == 0
        pulled["depth"] += 1
        try:
            async for chunk in real(self):
                if outer:
                    pulled["bytes"] += len(chunk)
                yield chunk
        finally:
            pulled["depth"] -= 1

    monkeypatch.setattr(Request, "stream", spy)
    return pulled


class TestByteCaps:
    @pytest.mark.parametrize("name,cap", [("a.txt", TXT_MD_MAX_BYTES),
                                          ("a.md", TXT_MD_MAX_BYTES),
                                          ("a.json", JSON_MAX_BYTES)])
    def test_one_byte_over_is_refused_unread(self, client, stream_spy, name, cap):
        r = upload(client, name, b"x" * (cap + 1))
        assert r.status_code == 413
        assert r.json()["detail"] == E.FILE_TOO_LARGE
        assert stream_spy["calls"] == 0, "the body was read before the cap refused it"
        assert modules(client) == []

    def test_exact_caps_are_accepted(self, client, stream_spy):
        """Positive control. Exactly 96 KiB of text is also over the 24,000
        character cap, so the byte gate lets it through and the character
        gate is what answers; a 1 MiB JSON is accepted as a file."""
        r = upload(client, "a.txt", b"x" * TXT_MD_MAX_BYTES)
        assert r.status_code == 400 and r.json()["detail"] == E.TOO_LONG
        assert stream_spy["bytes"] == TXT_MD_MAX_BYTES
        body = json.dumps({"format": "elysium.prompt-modules", "version": 1,
                           "modules": [{"name": "n", "content": "c"}]})
        padded = body + " " * (JSON_MAX_BYTES - len(body))
        r = upload(client, "a.json", padded.encode())
        assert r.status_code == 201, r.text

    def test_a_chunked_body_without_length_is_refused_unread(self, client, stream_spy):
        def gen():
            for _ in range(4):
                yield b"y" * 4096
        r = upload(client, "a.txt", gen())
        assert r.status_code == 411
        assert r.json()["detail"] == E.LENGTH_REQUIRED
        assert stream_spy["calls"] == 0
        assert modules(client) == []

    def test_a_sender_that_understates_its_length_is_cut_off(self):
        """The running count: Content-Length says 10, the body keeps coming.
        Driven directly, because no well-behaved HTTP client will send it."""
        import anyio
        from routers import prompt_modules as R

        pulled = {"n": 0}

        class Liar:
            headers = {"content-length": "10"}

            async def stream(self):
                for _ in range(TXT_MD_MAX_BYTES // 4096 + 50):
                    pulled["n"] += 4096
                    yield b"y" * 4096

        async def go():
            return await R._read_capped(Liar(), TXT_MD_MAX_BYTES)
        with pytest.raises(E.PromptModuleError) as info:
            anyio.run(go)
        assert info.value.code == E.FILE_TOO_LARGE
        assert pulled["n"] <= TXT_MD_MAX_BYTES + 4096

        class Honest(Liar):
            async def stream(self):
                yield b"y" * 10
        # ground: an honest small body is read whole
        assert anyio.run(lambda: R._read_capped(Honest(), TXT_MD_MAX_BYTES)) == b"y" * 10

    def test_small_file_ground(self, client):
        r = upload(client, "a.txt", b"Keep it light.")
        assert r.status_code == 201
        assert [m["content"] for m in modules(client)] == ["Keep it light."]

    def test_character_cap_over_http(self, client):
        assert upload(client, "a.txt", b"x" * MODULE_MAX_CHARS).status_code == 201
        r = upload(client, "b.txt", b"x" * (MODULE_MAX_CHARS + 1))
        assert r.json()["detail"] == E.TOO_LONG

    def test_type_and_name_errors_are_named(self, client, stream_spy):
        assert upload(client, "a.pdf", b"x").json()["detail"] == E.FILE_TYPE_UNSUPPORTED
        r = client.post(f"{BASE}/import", content=b"x",
                        headers={"Content-Type": "application/octet-stream"})
        assert r.json()["detail"] == E.FILENAME_MISSING
        assert stream_spy["calls"] == 0


# ---------------------------------------------------------------------------
# MT-9: nothing on disk
# ---------------------------------------------------------------------------

@pytest.fixture()
def disk_spy(monkeypatch):
    """Every Python-level way this process could put upload bytes on disk."""
    writes: list[str] = []

    def wrap(owner, attr, label):
        real = getattr(owner, attr)

        def spy(*a, **k):
            writes.append(label)
            return real(*a, **k)
        monkeypatch.setattr(owner, attr, spy)

    for attr in ("mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryFile"):
        wrap(tempfile, attr, f"tempfile.{attr}")
    real_rollover = tempfile.SpooledTemporaryFile.rollover

    def rollover(self):
        writes.append("SpooledTemporaryFile.rollover")
        return real_rollover(self)
    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", rollover)

    real_open = builtins.open

    def spy_open(file, mode="r", *a, **k):
        if any(c in mode for c in "wax+"):
            writes.append(f"open:{file}")
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_open)

    real_os_open = os.open

    def spy_os_open(path, flags, *a, **k):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            writes.append(f"os.open:{path}")
        return real_os_open(path, flags, *a, **k)
    monkeypatch.setattr(os, "open", spy_os_open)
    return writes


class TestNoDisk:
    def test_an_upload_writes_no_file(self, client, disk_spy):
        big = json.dumps({"format": "elysium.prompt-modules", "version": 1,
                          "modules": [{"name": f"m{i}", "content": "w " * 5000}
                                      for i in range(60)]}).encode()
        assert len(big) > 512 * 1024   # past any in-memory spool threshold
        r = upload(client, "big.json", big)
        assert r.status_code == 201, r.text
        assert disk_spy == [], disk_spy
        # positive control: the vault holds the rows
        with database.get_db() as con:
            n = con.execute("SELECT COUNT(*) FROM prompt_modules").fetchone()[0]
        assert n == 60

    def test_the_spy_sees_a_temp_file(self, disk_spy):
        """The spy is not blind: a real temp file is recorded."""
        with tempfile.TemporaryFile() as fh:
            fh.write(b"x")
        assert disk_spy

    def test_multipart_is_refused_before_reading(self, client, stream_spy):
        r = client.post(f"{BASE}/import", files={"file": ("a.txt", b"x" * 2000, "text/plain")},
                        headers={FILENAME_HEADER: "a.txt"})
        assert r.status_code == 415
        assert r.json()["detail"] == E.UPLOAD_RAW_ONLY
        assert stream_spy["calls"] == 0


# ---------------------------------------------------------------------------
# List, privacy of the kept copy
# ---------------------------------------------------------------------------

ST_DOC = {
    "proxy_password": "never-returned-pw",
    "temperature": 1.0,
    "prompts": [
        {"identifier": "a", "name": "Alpha", "content": "Alpha text."},
        {"identifier": "b", "name": "Beta", "content": "Beta text."},
        {"identifier": "c", "name": "Gamma", "content": "Gamma text."},
        {"identifier": "d", "name": "Delta", "content": "Delta text."},
        {"identifier": "e", "name": "Epsilon", "content": "Epsilon text."},
        {"identifier": "imp", "name": "Only impersonate", "content": "Imp text.",
         "injection_trigger": ["impersonate"]},
        {"identifier": "chatHistory", "marker": True},
    ],
    "prompt_order": [{"character_id": 100001, "order": [
        {"identifier": "a", "enabled": True},
        {"identifier": "b", "enabled": False},
        {"identifier": "c", "enabled": True},
        {"identifier": "d", "enabled": False},
        {"identifier": "e", "enabled": True},
        {"identifier": "imp", "enabled": True},
        {"identifier": "chatHistory", "enabled": True},
    ]}],
}


def import_st(client, doc=None) -> dict:
    r = upload(client, "preset.json", json.dumps(doc or ST_DOC).encode())
    assert r.status_code == 201, r.text
    return r.json()


class TestListAndPrivacy:
    def test_list_never_carries_the_kept_copy_or_raw_entries(self, client):
        out = import_st(client)
        listing = client.get(BASE).text
        assert "never-returned-pw" not in listing
        assert "raw_entry_json" not in listing and "preset_json" not in listing
        assert "raw_entry_json" not in json.dumps(out)
        assert out["report"]["modules_created"] == 6
        # ground: the vault holds the entry JSON the list leaves out
        with database.get_db() as con:
            raw = con.execute("SELECT raw_entry_json FROM prompt_modules "
                              "WHERE st_identifier = 'a'").fetchone()[0]
            kept = con.execute("SELECT preset_json FROM prompt_module_imports").fetchone()[0]
        assert json.loads(raw)["name"] == "Alpha"
        assert "never-returned-pw" not in kept and '"temperature": 1.0' in kept

    def test_everything_starts_off_with_preset_state_kept(self, client):
        import_st(client)
        rows = {m["st_identifier"]: m for m in modules(client)}
        assert not any(m["enabled"] for m in rows.values())
        assert [rows[k]["st_enabled"] for k in "abcde"] == [True, False, True, False, True]
        assert rows["chatHistory"]["kind"] == "marker"
        assert rows["chatHistory"]["can_enable"] is False
        assert client.get(BASE).json()["enabled_count"] == 0

    def test_nothing_is_logged(self, client, caplog):
        caplog.set_level("DEBUG")
        import_st(client)
        joined = "\n".join(r.getMessage() for r in caplog.records)
        for secret in ("Alpha", "Alpha text.", "never-returned-pw", "preset.json"):
            assert secret not in joined
        assert "import_id=" in joined   # positive control: the id line is there


# ---------------------------------------------------------------------------
# PATCH, delete
# ---------------------------------------------------------------------------

class TestPatch:
    def test_enable_disable_round_trip(self, client):
        upload(client, "a.txt", b"One.")
        mid = modules(client)[0]["id"]
        r = client.patch(f"{BASE}/{mid}", json={"enabled": True})
        assert r.status_code == 200 and r.json()["enabled"] is True
        with database.get_db() as con:
            assert [m["id"] for m in store.list_enabled(con)] == [mid]
        r = client.patch(f"{BASE}/{mid}", json={"enabled": False})
        assert r.json()["enabled"] is False
        with database.get_db() as con:
            assert store.list_enabled(con) == []

    def test_never_triggering_entry_cannot_be_enabled(self, client):
        import_st(client)
        imp = next(m for m in modules(client) if m["st_identifier"] == "imp")
        assert imp["blocked_reason"] == store.BLOCK_NEVER_TRIGGERS
        r = client.patch(f"{BASE}/{imp['id']}", json={"enabled": True})
        assert r.status_code == 409
        assert r.json() == {"detail": E.CANNOT_ENABLE, "reason": store.BLOCK_NEVER_TRIGGERS}
        # positive control: an ordinary entry from the same file enables
        a = next(m for m in modules(client) if m["st_identifier"] == "a")
        assert client.patch(f"{BASE}/{a['id']}", json={"enabled": True}).status_code == 200

    def test_marker_and_over_cap_rows_cannot_be_enabled(self, client):
        doc = json.loads(json.dumps(ST_DOC))
        doc["prompts"][0]["content"] = "z" * (MODULE_MAX_CHARS + 1)
        import_st(client, doc)
        rows = {m["st_identifier"]: m for m in modules(client)}
        for ident, reason in (("chatHistory", store.BLOCK_MARKER),
                              ("a", store.BLOCK_OVER_CAP)):
            r = client.patch(f"{BASE}/{rows[ident]['id']}", json={"enabled": True})
            assert r.status_code == 409 and r.json()["reason"] == reason

    def test_regenerate_only_entry_is_filtered_by_generation(self, client):
        doc = {"prompts": [
            {"identifier": "s", "name": "S", "content": "Send.", "injection_trigger": ["normal"]},
            {"identifier": "r", "name": "R", "content": "Regen.", "injection_trigger": ["swipe"]}]}
        import_st(client, doc)
        for m in modules(client):
            client.patch(f"{BASE}/{m['id']}", json={"enabled": True})
        with database.get_db() as con:
            assert [m["content"] for m in store.list_enabled(con, "send")] == ["Send."]
            assert [m["content"] for m in store.list_enabled(con, "regenerate")] == ["Regen."]
            assert len(store.list_enabled(con)) == 2   # H4 counts both

    def test_rename_and_revision(self, client):
        upload(client, "a.txt", b"One.")
        m = modules(client)[0]
        r = client.patch(f"{BASE}/{m['id']}", json={"name": "Renamed"})
        assert r.json()["name"] == "Renamed" and r.json()["revision"] == m["revision"] + 1
        for bad in ("", "   ", "n" * 65):
            r = client.patch(f"{BASE}/{m['id']}", json={"name": bad})
            assert r.json()["detail"] == E.NAME_INVALID

    def test_move_renumbers_contiguously(self, client):
        for n in ("a", "b", "c"):
            upload(client, f"{n}.txt", n.encode())
        ids = [m["id"] for m in modules(client)]
        client.patch(f"{BASE}/{ids[2]}", json={"sort_order": 0})
        after = modules(client)
        assert [m["id"] for m in after] == [ids[2], ids[0], ids[1]]
        assert [m["sort_order"] for m in after] == [0, 1, 2]
        with database.get_db() as con:
            for i in ids:
                store.set_enabled(con, [i], True)
            assert [m["id"] for m in store.list_enabled(con)] == [ids[2], ids[0], ids[1]]

    def test_bad_bodies_are_named(self, client):
        upload(client, "a.txt", b"One.")
        mid = modules(client)[0]["id"]
        for body in ({}, {"colour": 1}, {"enabled": "yes"}, {"sort_order": "1"},
                     {"enabled": True, "max_tokens": -1}):
            r = client.patch(f"{BASE}/{mid}", json=body)
            assert r.status_code == 400 and r.json()["detail"] == E.REQUEST_INVALID, body
        assert client.patch(f"{BASE}/999", json={"enabled": True}).status_code == 404

    def test_delete_last_entry_removes_the_import(self, client):
        out = import_st(client, {"prompts": [
            {"identifier": "x", "name": "X", "content": "Only one."},
            {"identifier": "chatHistory", "marker": True}]})
        real = [m for m in out["modules"] if m["kind"] == "module"]
        assert client.delete(f"{BASE}/{real[0]['id']}").status_code == 200
        assert modules(client) == []
        with database.get_db() as con:
            assert con.execute("SELECT COUNT(*) FROM prompt_module_imports").fetchone()[0] == 0
        assert client.delete(f"{BASE}/{real[0]['id']}").status_code == 404


# ---------------------------------------------------------------------------
# H3: enable preset defaults
# ---------------------------------------------------------------------------

class TestEnablePresetDefaults:
    def test_turns_on_exactly_what_sillytavern_had_on(self, client):
        out = import_st(client)
        r = client.post(f"{BASE}/enable-preset-defaults", json={"import_id": out["import_id"]})
        assert r.status_code == 200, r.text
        on = {m["st_identifier"] for m in modules(client) if m["enabled"]}
        assert on == {"a", "c", "e"}
        # the never-triggering entry was on in ST and is listed, not enabled
        imp = next(m for m in modules(client) if m["st_identifier"] == "imp")
        assert r.json()["left_out"] == [{"id": imp["id"], "reason": store.BLOCK_NEVER_TRIGGERS}]

    def test_over_budget_set_turns_on_nothing(self, client):
        doc = json.loads(json.dumps(ST_DOC))
        for p in doc["prompts"][:5]:
            p["content"] = "w" * 12000
        out = import_st(client, doc)
        r = client.post(f"{BASE}/enable-preset-defaults", json={
            "import_id": out["import_id"], "context_budget_tokens": 16384,
            "max_tokens": 1024})
        assert r.status_code == 400
        assert r.json()["detail"] == E.OVER_BUDGET
        assert not any(m["enabled"] for m in modules(client))
        # positive control: a window large enough takes the whole set
        r = client.post(f"{BASE}/enable-preset-defaults", json={
            "import_id": out["import_id"], "context_budget_tokens": 32000,
            "max_tokens": 1024, "model_id": None})
        assert r.status_code == 200, r.text
        assert {m["st_identifier"] for m in modules(client) if m["enabled"]} == {"a", "c", "e"}

    def test_unknown_import_is_named(self, client):
        r = client.post(f"{BASE}/enable-preset-defaults", json={"import_id": 42})
        assert r.status_code == 404 and r.json()["detail"] == E.IMPORT_NOT_FOUND


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class TestSettings:
    def test_defaults(self, client):
        assert client.get(f"{BASE}/settings").json() == {
            "prompt_layer_enabled": False, "reply_length_words": None}

    @pytest.mark.parametrize("words", [150, 300, None])
    def test_allowed_values_round_trip(self, client, words):
        r = client.put(f"{BASE}/settings", json={"prompt_layer_enabled": True,
                                                 "reply_length_words": words})
        assert r.status_code == 200
        got = client.get(f"{BASE}/settings").json()
        assert got == {"prompt_layer_enabled": True, "reply_length_words": words}
        with database.get_db() as con:
            assert store.layer_enabled(con) is True
            assert store.reply_length_words(con) == words

    @pytest.mark.parametrize("body", [
        {"prompt_layer_enabled": True, "reply_length_words": 200},
        {"prompt_layer_enabled": "yes", "reply_length_words": None},
        {"prompt_layer_enabled": True, "reply_length_words": True},
        {"prompt_layer_enabled": True},
        {"prompt_layer_enabled": True, "reply_length_words": None, "x": 1},
    ])
    def test_invalid_values_are_refused_and_change_nothing(self, client, body):
        r = client.put(f"{BASE}/settings", json=body)
        assert r.status_code == 400 and r.json()["detail"] == E.SETTINGS_INVALID
        assert client.get(f"{BASE}/settings").json()["prompt_layer_enabled"] is False

