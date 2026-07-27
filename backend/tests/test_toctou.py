"""v1.1 FB3: guard-then-mutate TOCTOU holes converted from 500 to 404/409.

A true two-connection race cannot interleave under BEGIN IMMEDIATE, so the
deterministic simulation is a SAME-connection interleave: an instrumented
get_db proxy deletes the guarded row immediately after the guard SELECT
returns (precedent: test_edit_message.py's mid_stream hook). This exercises
both the defensive None->404 re-SELECT and the IntegrityError->404 mapping.
"""

import database

from conftest import make_character, make_chat, make_persona


class _InterleaveConn:
    """Wraps a real keyed connection; after the Nth execute() whose SQL starts
    with a SELECT, runs `hook(con)` once (the racing delete)."""

    def __init__(self, con, hook, after_selects=1):
        self._con = con
        self._hook = hook
        self._after = after_selects
        self._selects = 0
        self._fired = False

    def execute(self, sql, *args, **kwargs):
        cur = self._con.execute(sql, *args, **kwargs)
        if (
            not self._fired
            and sql.lstrip().upper().startswith("SELECT")
        ):
            self._selects += 1
            if self._selects >= self._after:
                self._fired = True
                self._hook(self._con)
        return cur

    def __getattr__(self, name):
        return getattr(self._con, name)


def _patch_get_db_with_interleave(monkeypatch, module, hook, after_selects=1):
    import contextlib
    real_get_db = database.get_db

    @contextlib.contextmanager
    def proxy():
        with real_get_db() as con:
            yield _InterleaveConn(con, hook, after_selects)

    monkeypatch.setattr(module, "get_db", proxy)


def test_rename_chat_racing_delete_returns_404(client, monkeypatch):
    import routers.chats as chats_router

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    def race(con):
        # Delete the chat right after the guard SELECT (same connection/txn).
        con.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        con.execute("DELETE FROM chats WHERE id = ?", (chat_id,))

    _patch_get_db_with_interleave(monkeypatch, chats_router, race)
    resp = client.patch(f"/api/v1/chats/{chat_id}", json={"title": "New"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "chat_not_found"


def test_create_chat_racing_character_delete_returns_404(client, monkeypatch):
    import routers.chats as chats_router

    char_id = make_character(client)

    def race(con):
        con.execute("DELETE FROM characters WHERE id = ?", (char_id,))

    _patch_get_db_with_interleave(monkeypatch, chats_router, race)
    resp = client.post("/api/v1/chats", json={"character_id": char_id})
    # FK trips on the INSERT -> mapped to a clean 404, not a 500.
    assert resp.status_code == 404
    assert resp.json()["detail"] == "character_not_found"


def test_patch_character_racing_delete_returns_404(client, monkeypatch):
    import routers.characters as characters_router

    char_id = make_character(client)

    def race(con):
        con.execute("DELETE FROM chats WHERE character_id = ?", (char_id,))
        con.execute("DELETE FROM characters WHERE id = ?", (char_id,))

    _patch_get_db_with_interleave(monkeypatch, characters_router, race)
    resp = client.patch(
        f"/api/v1/characters/{char_id}", json={"name": "Renamed"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "character_not_found"


def test_patch_persona_racing_delete_returns_404(client, monkeypatch):
    import routers.personas as personas_router

    pid = make_persona(client, "Nova", "desc")

    def race(con):
        con.execute("DELETE FROM personas WHERE id = ?", (pid,))

    _patch_get_db_with_interleave(monkeypatch, personas_router, race)
    resp = client.patch(
        f"/api/v1/personas/{pid}", json={"display_name": "Renamed"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "persona_not_found"


# ── Happy-path regressions: BEGIN IMMEDIATE added no behavior change ──────────

def test_rename_create_patch_happy_paths_unchanged(client):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    r = client.patch(f"/api/v1/chats/{chat_id}", json={"title": "Renamed"})
    assert r.status_code == 200 and r.json()["title"] == "Renamed"

    r = client.post("/api/v1/chats", json={"character_id": char_id, "title": "C2"})
    assert r.status_code == 201 and r.json()["title"] == "C2"

    r = client.patch(f"/api/v1/characters/{char_id}", json={"name": "NewName"})
    assert r.status_code == 200 and r.json()["name"] == "NewName"

    pid = make_persona(client, "P")
    r = client.patch(f"/api/v1/personas/{pid}", json={"description": "d"})
    assert r.status_code == 200 and r.json()["description"] == "d"
