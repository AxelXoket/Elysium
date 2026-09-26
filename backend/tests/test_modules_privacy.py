"""Prompt modules: what reaches the disk, the wire and the log.

Canaries are unique strings planted in an imported file. Each test that
asserts an absence has a positive control showing its detector finds the
canary where it IS (the decrypted vault, a plaintext file, a log line).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import quote

import pytest

import database
from prompt_modules import errors as E
from prompt_modules import parsers
from prompt_modules.limits import FILENAME_HEADER

BASE = "/api/v1/prompt-modules"

NAME = "CanaryNameQ7x"
CONTENT = "CanaryContentQ7x stays in the vault."
RAW = "CanaryRawFieldQ7x"
FILE = "CanaryFileQ7x.json"
SECRET = "CanarySecretQ7x"

DOC = {
    "proxy_password": SECRET + "-pw",
    "extensions": {"deep": {"api_key": SECRET + "-key"}},
    "temperature": 0.9,
    "prompts": [
        {"identifier": "c1", "name": NAME, "content": CONTENT,
         "custom_field": RAW, "access_token": SECRET + "-tok"},
        {"identifier": "c2", "name": "Plain", "content": "Plain text."},
    ],
    "prompt_order": [{"character_id": 100001, "order": [
        {"identifier": "c1", "enabled": True},
        {"identifier": "c2", "enabled": True}]}],
}
CANARIES = (NAME, "CanaryContentQ7x", RAW, "CanaryFileQ7x")


def _import(client):
    r = client.post(f"{BASE}/import", content=json.dumps(DOC).encode(),
                    headers={FILENAME_HEADER: quote(FILE),
                             "Content-Type": "application/octet-stream"})
    assert r.status_code == 201, r.text
    return r


def _found(blob: bytes, needle: str) -> bool:
    return needle.encode("utf-8") in blob or needle.encode("utf-16-le") in blob


def _disk_bytes(folder: Path) -> bytes:
    return b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())


class TestDisk:
    def test_names_content_and_raw_entries_only_exist_encrypted(self, client, db):
        _import(client)
        folder = Path(db).parent
        blob = _disk_bytes(folder)
        for c in CANARIES:
            assert not _found(blob, c), f"{c} is on disk in the clear"
        # positive control 1: the vault really holds them (decrypted read)
        with database.get_db() as con:
            rows = json.dumps([dict(r) for r in con.execute(
                "SELECT name, content, raw_entry_json, source_filename "
                "FROM prompt_modules").fetchall()])
        for c in CANARIES:
            assert c in rows, c
        # positive control 2: the detector finds a canary written in the clear
        probe = folder / "probe.txt"
        probe.write_text(NAME, encoding="utf-8")
        assert _found(_disk_bytes(folder), NAME)
        probe.unlink()

    def test_preset_secrets_are_absent_from_every_stored_row(self, client):
        _import(client)
        with database.get_db() as con:
            everything = json.dumps(
                [dict(r) for r in con.execute("SELECT * FROM prompt_modules")]
                + [dict(r) for r in con.execute("SELECT * FROM prompt_module_imports")])
        assert SECRET not in everything
        # ground: the non-secret neighbours are stored
        assert '"temperature": 0.9' in everything or "0.9" in everything
        assert RAW in everything


class TestWire:
    def test_import_and_list_never_return_raw_entry_json(self, client):
        imported = _import(client).text
        listed = client.get(BASE).text
        for text in (imported, listed):
            assert RAW not in text
            assert SECRET not in text
            assert "raw_entry_json" not in text and "preset_json" not in text
        # positive control: the key NAME is reported, the value is not
        assert "custom_field" in imported
        assert NAME in listed


class TestLogs:
    def _all_calls(self, client):
        out = _import(client).json()
        rows = client.get(BASE).json()["modules"]
        c1 = next(m for m in rows if m["st_identifier"] == "c1")
        client.patch(f"{BASE}/{c1['id']}", json={"name": NAME + "-renamed"})
        client.patch(f"{BASE}/{c1['id']}", json={"enabled": True})
        client.post(f"{BASE}/enable-preset-defaults", json={"import_id": out["import_id"]})
        client.put(f"{BASE}/settings", json={"prompt_layer_enabled": True,
                                             "reply_length_words": 150})
        for m in rows:
            client.delete(f"{BASE}/{m['id']}")

    def test_no_name_content_or_filename_in_any_log(self, client, caplog):
        caplog.set_level(logging.DEBUG)
        self._all_calls(client)
        text = "\n".join(r.getMessage() for r in caplog.records)
        for c in CANARIES + (SECRET,):
            assert c not in text, c
        assert "import_id=" in text and "Prompt module deleted" in text

    def test_the_log_detector_is_not_blind(self, caplog):
        caplog.set_level(logging.DEBUG)
        logging.getLogger("routers.prompt_modules").info("leak %s", NAME)
        assert NAME in "\n".join(r.getMessage() for r in caplog.records)

    def test_a_parser_crash_is_named_and_logs_only_its_type(
            self, client, caplog, monkeypatch):
        caplog.set_level(logging.DEBUG)

        def boom(filename, data):
            raise ValueError("CanaryCrashQ7x " + filename)
        monkeypatch.setattr(parsers, "parse_upload", boom)
        r = client.post(f"{BASE}/import", content=b"{}",
                        headers={FILENAME_HEADER: quote(FILE),
                                 "Content-Type": "application/octet-stream"})
        assert r.status_code == 400
        assert r.json()["detail"] == E.JSON_INVALID
        assert "CanaryCrashQ7x" not in r.text
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "CanaryCrashQ7x" not in text and "CanaryFileQ7x" not in text
        assert "ValueError" in text      # positive control: the type is logged


def test_superscript_digits_over_http_are_not_a_500(client):
    """W2 M1 regression, end to end."""
    doc = {"prompts": [{"identifier": "a", "name": "A", "content": "x",
                        "injection_depth": "12\u00b2"}]}
    r = client.post(f"{BASE}/import", content=json.dumps(doc).encode(),
                    headers={FILENAME_HEADER: "p.json",
                             "Content-Type": "application/octet-stream"})
    assert r.status_code == 201, r.text
    assert r.json()["modules"][0]["st_injection_depth"] is None


@pytest.mark.parametrize("path", ["/export", "/1/copy"])
def test_routes_outside_the_contract_do_not_exist(client, path):
    method = client.get if path == "/export" else client.post
    assert method(f"{BASE}{path}").status_code in (404, 405)
