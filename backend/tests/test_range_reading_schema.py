"""Baslik 8 step 1: range reading's single schema record, and the two exact
marks it lets the app write today.

Decision 59 a puts every new field and table in ONE record under stamp 6 and
re-records the version 6 fingerprint once. Decisions 15 a and 60 a replace a
guess ("updated_at moved past created_at", which an edit, an accept, a pin
and a drag all produce) with two exact fields. Decision 70 a adds a table for
a keyed digest of a hand-deleted note, never its text.

Three groups, each with its ground and its positive control:

  * the migration, run against a vault built by the PREVIOUS schema code
    (tests/fixtures/schema6_before_range_reading.sql, whose fingerprint is
    checked against the recorded previous value before anything else);
  * the two marks, driven through the real routes;
  * the two tables' own rules, their lifetime, and the digest.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import database
import notebook_store as notebook
import secrets_service
import vault_state
from tests.conftest import TEST_VAULT_KEY
from tests.test_schema_fingerprint import (
    FINGERPRINT_6_BEFORE_RANGE_READING, FINGERPRINTS, _fingerprint)

API = "/api/v1/notebook"
FIXTURE = Path(__file__).parent / "fixtures" / "schema6_before_range_reading.sql"

#: Every column the record adds, as (table, column). Built from the
#: migration's own tuples, never retyped.
_NEW_COLUMNS = (
    [("notebook_entries", c) for c, _ in database._RANGE_READING_ENTRY_COLUMNS]
    + [("notebook_extractions", c)
       for c, _ in database._RANGE_READING_EXTRACTION_COLUMNS])


def _columns(con, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


def _objects(con) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
    ).fetchall()}


def _ddl_for_the_record(statements: list[str]) -> list[str]:
    """The statements that ADD one of the record's columns or CREATE one of
    its tables or indexes. One definition for the absence assertion and its
    positive control, so the two cannot drift."""
    names = ([c for _, c in _NEW_COLUMNS]
             + list(database._RANGE_READING_TABLES)
             + list(database._RANGE_READING_INDEXES))
    out = []
    for s in statements:
        head = s.lstrip().upper()
        if not (head.startswith("CREATE") or "ADD COLUMN" in head):
            continue
        if head.startswith("CREATE") and "IF NOT EXISTS" in head:
            continue                      # the older siblings' idempotent DDL
        if any(n in s for n in names):
            out.append(s)
    return out


def undo_range_reading(con) -> None:
    """The rollback `database._migrate_range_reading` documents, in its
    order: indexes, then columns, then tables. Shared with
    test_notebook_schema, whose own rollback has to take this record off
    first now that it sits after the backfill additions."""
    con.execute("BEGIN IMMEDIATE")
    for index in database._RANGE_READING_INDEXES:
        con.execute(f"DROP INDEX {index}")
    for table, column in reversed(_NEW_COLUMNS):
        con.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    for table in database._RANGE_READING_TABLES:
        con.execute(f"DROP TABLE {table}")
    con.execute("COMMIT")


def _rows(con, table: str, columns: list[str]) -> list[tuple]:
    cols = ", ".join(columns)
    return [tuple(r) for r in con.execute(
        f"SELECT {cols} FROM {table} ORDER BY rowid").fetchall()]


# ---------------------------------------------------------------------------
# the migration, from a vault the previous code built
# ---------------------------------------------------------------------------

@pytest.fixture()
def old_vault(tmp_path, monkeypatch, request) -> str:
    """An encrypted vault holding exactly what the previous build wrote."""
    import config

    path = str(tmp_path / "old_app.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(database, "DB_PATH", path)
    con = database.sqlite3.connect(path)
    try:
        database._key_pragma(con, TEST_VAULT_KEY)
        con.execute("PRAGMA foreign_keys = OFF")
        con.executescript(FIXTURE.read_text(encoding="utf-8"))
        con.commit()
    finally:
        con.close()
    request.addfinalizer(vault_state.clear_key)
    vault_state.set_key(TEST_VAULT_KEY)
    return path


def _open(path: str):
    con = database.sqlite3.connect(path)
    database._key_pragma(con, TEST_VAULT_KEY)
    con.row_factory = database.sqlite3.Row
    return con


class TestTheMigrationFromThePreviousShape:
    def _snapshot(self, con) -> dict[str, list[tuple]]:
        """Every row of every table the previous shape had, in the columns
        it had. "Nothing old changed" is a claim about exactly these."""
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "ORDER BY name").fetchall()]
        old_cols = {t: [c for c in _columns(con, t)
                        if (t, c) not in _NEW_COLUMNS] for t in tables}
        out = {t: _rows(con, t, old_cols[t]) for t in tables
               if t not in database._RANGE_READING_TABLES}
        # The one row the record ADDS to an old table, taken out here and
        # asserted on its own below: "nothing old changed" is about the rows
        # that were there, and a new key is not one of them.
        out["settings"] = [r for r in out["settings"]
                           if r[0] != database.HAND_MARKS_SINCE_KEY]
        return out

    def test_the_fixture_is_the_previous_shape(self, old_vault) -> None:
        """GROUND for every test in this class: without it the rest could
        be migrating a shape nobody ever shipped."""
        con = _open(old_vault)
        try:
            assert _fingerprint(con) == FINGERPRINT_6_BEFORE_RANGE_READING
            assert con.execute("PRAGMA user_version").fetchone()[0] == 6
            objects = _objects(con)
            for table, column in _NEW_COLUMNS:
                assert column not in _columns(con, table)
            for name in (*database._RANGE_READING_TABLES,
                         *database._RANGE_READING_INDEXES):
                assert name not in objects
            # Positive control: the fixture really carries data to preserve.
            assert con.execute(
                "SELECT COUNT(*) FROM notebook_entries").fetchone()[0] == 7
        finally:
            con.close()

    def test_it_arrives_at_the_recorded_shape_and_keeps_every_row(
            self, old_vault) -> None:
        con = _open(old_vault)
        before = self._snapshot(con)
        con.close()

        database.init_db()

        con = _open(old_vault)
        try:
            assert con.execute("PRAGMA user_version").fetchone()[0] == 6, (
                "59 a: the stamp stays 6")
            assert _fingerprint(con) == FINGERPRINTS[6]
            assert self._snapshot(con) == before, "an old row changed"
            # ... and the one new row is there, later than every old note,
            # so an undo can tell the old notes from the new ones.
            since = con.execute(
                "SELECT value FROM settings WHERE key = ?",
                (database.HAND_MARKS_SINCE_KEY,)).fetchone()
            assert since is not None, "the marks-since moment was not written"
            newest = con.execute(
                "SELECT MAX(created_at) FROM notebook_entries").fetchone()[0]
            assert newest < since[0]
            for table in database._RANGE_READING_TABLES:
                assert con.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        finally:
            con.close()

    def test_it_fills_nothing_it_cannot_prove(self, old_vault) -> None:
        """The old "edited" signal is NOT copied into the exact field.

        POSITIVE CONTROL first: the fixture holds a note whose updated_at
        moved past created_at - the guess 15 a replaces - and extraction
        rows a reader-model fill could have targeted. Only then is the
        absence meaningful."""
        con = _open(old_vault)
        guessed = con.execute(
            "SELECT COUNT(*) FROM notebook_entries "
            "WHERE updated_at <> created_at").fetchone()[0]
        reads = con.execute(
            "SELECT COUNT(*) FROM notebook_extractions").fetchone()[0]
        con.close()
        assert guessed >= 1 and reads >= 1

        database.init_db()

        con = _open(old_vault)
        try:
            for table, column in _NEW_COLUMNS:
                filled = con.execute(
                    f"SELECT COUNT(*) FROM {table} "
                    f"WHERE {column} IS NOT NULL").fetchone()[0]
                assert filled == 0, f"{table}.{column} was filled on open"
            # The per-vault trace key is minted on first USE, not on open.
            assert secrets_service.get_secret(
                notebook.TRACE_KEY_SECRET, conn=con) is None
        finally:
            con.close()

    def test_a_second_and_third_run_fire_no_ddl(self, old_vault) -> None:
        first: list[str] = []
        con = _open(old_vault)
        try:
            con.set_trace_callback(first.append)
            database._migrate_notebook(con)
            con.set_trace_callback(None)
            con.commit()
            shape = _fingerprint(con)
        finally:
            con.close()
        # POSITIVE CONTROL for the filter: on the previous shape it finds
        # every addition, five columns, two tables, two indexes.
        assert len(_ddl_for_the_record(first)) == (
            len(_NEW_COLUMNS) + len(database._RANGE_READING_TABLES)
            + len(database._RANGE_READING_INDEXES))
        assert shape == FINGERPRINTS[6]

        database.init_db()                         # the second unlock
        again: list[str] = []
        con = _open(old_vault)
        try:
            con.set_trace_callback(again.append)
            database._migrate_notebook(con)        # and a third, traced
            con.set_trace_callback(None)
            con.commit()
            assert _fingerprint(con) == shape
        finally:
            con.close()
        assert _ddl_for_the_record(again) == []
        # Ground for the trace itself: the third run did run.
        assert any("PRAGMA table_info(notebook_extractions)" in s
                   for s in again)

    def test_the_documented_rollback_returns_the_previous_shape(
            self, old_vault) -> None:
        database.init_db()
        con = _open(old_vault)
        try:
            con.isolation_level = None
            undo_range_reading(con)
            assert _fingerprint(con) == FINGERPRINT_6_BEFORE_RANGE_READING
        finally:
            con.close()
        database.init_db()                         # and forward again
        con = _open(old_vault)
        try:
            assert _fingerprint(con) == FINGERPRINTS[6]
        finally:
            con.close()


class TestOrphansOfTheNewTables:
    def test_rows_whose_chat_is_gone_are_swept(self, db) -> None:
        with database.get_db() as con:
            con.execute("INSERT INTO characters (name) VALUES ('C')")
            con.execute("INSERT INTO chats (character_id, title) "
                        "VALUES ((SELECT MAX(id) FROM characters), 'kept')")
            kept = con.execute("SELECT MAX(id) FROM chats").fetchone()[0]
        con = database.sqlite3.connect(db)
        try:
            database._key_pragma(con, TEST_VAULT_KEY)
            con.execute("PRAGMA foreign_keys = OFF")
            for chat in (kept, 999):
                con.execute(
                    "INSERT INTO notebook_chunk_reads (chat_id, chunk_no, "
                    "from_message_id, to_message_id) VALUES (?, 1, 1, 20)",
                    (chat,))
                con.execute(
                    "INSERT INTO notebook_deleted_traces (chat_id, digest) "
                    "VALUES (?, ?)", (chat, "0" * 64))
            con.commit()
        finally:
            con.close()
        database.init_db()
        with database.get_db() as con:
            for table in database._RANGE_READING_TABLES:
                chats = [r[0] for r in con.execute(
                    f"SELECT chat_id FROM {table}").fetchall()]
                assert chats == [kept], (table, chats)


# ---------------------------------------------------------------------------
# the two exact marks
# ---------------------------------------------------------------------------

def _chat(con) -> int:
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    ch = con.execute("SELECT MAX(id) FROM characters").fetchone()[0]
    con.execute("INSERT INTO chats (character_id, title) VALUES (?, 't')", (ch,))
    return con.execute("SELECT MAX(id) FROM chats").fetchone()[0]


def _model_note(con, chat: int, text: str, status: str) -> int:
    pos = con.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM "
                      "notebook_entries WHERE chat_id = ?", (chat,)
                      ).fetchone()[0]
    return con.execute(
        "INSERT INTO notebook_entries (chat_id, position, text, status, "
        "provenance) VALUES (?, ?, ?, ?, 'model')",
        (chat, pos, text, status)).lastrowid


def _marks(entry_id: int) -> tuple:
    with database.get_db() as con:
        r = con.execute("SELECT edited_at, hand_accepted_at FROM "
                        "notebook_entries WHERE id = ?", (entry_id,)).fetchone()
    return r["edited_at"], r["hand_accepted_at"]


@pytest.fixture()
def notes(db):
    with database.get_db() as con:
        chat = _chat(con)
        accepted = _model_note(con, chat, "Mira keeps the mill.", "accepted")
        proposed = _model_note(con, chat, "Demir owns a boat.", "proposed")
    return chat, accepted, proposed


class TestEditedByHand:
    def test_a_text_change_through_patch_sets_it(self, client, notes) -> None:
        chat, note, _ = notes
        assert _marks(note) == (None, None)            # ground
        r = client.patch(f"{API}/entries/{note}?chat_id={chat}",
                         json={"text": "Mira sold the mill."})
        assert r.status_code == 200, r.text
        edited, accepted = _marks(note)
        assert edited is not None
        assert accepted is None, "an edit is not an acceptance"
        assert r.json()["edited_at"] == edited

    def test_the_stamp_is_utc_now_in_the_documented_shape(
            self, client, notes) -> None:
        chat, note, _ = notes
        r = client.patch(f"{API}/entries/{note}?chat_id={chat}",
                         json={"text": "Mira sold the mill."})
        assert r.status_code == 200, r.text
        edited = _marks(note)[0]
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", edited), edited
        # SQLite's `datetime('now')` is UTC; a local-time or shifted stamp
        # would sit hours away from it.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stamp = datetime.strptime(edited, "%Y-%m-%d %H:%M:%S")
        assert abs((now - stamp).total_seconds()) < 60, (edited, now)

    @pytest.mark.parametrize("words", [
        "Mira keeps the mill.",            # the same words
        "Mira  keeps the   mill.",         # re-spaced
        "  Mira keeps	the mill.  ",       # tabs and ends
    ])
    def test_the_same_words_sent_back_are_not_an_edit(
            self, client, notes, words) -> None:
        """Whitespace alone is not an edit: trimmed, runs collapsed."""
        chat, note, _ = notes
        r = client.patch(f"{API}/entries/{note}?chat_id={chat}",
                         json={"text": words})
        assert r.status_code == 200, r.text
        # POSITIVE CONTROL: the PATCH reached the write - the stored text is
        # what was sent and the row's update stamp moved.
        with database.get_db() as con:
            row = con.execute("SELECT text, created_at, updated_at FROM "
                              "notebook_entries WHERE id = ?", (note,)
                              ).fetchone()
        assert row["text"] == words.strip()        # what was sent, stored
        assert row["updated_at"] != row["created_at"]
        assert _marks(note)[0] is None

    def test_an_invisible_character_is_not_an_edit(
            self, client, notes) -> None:
        chat, note, _ = notes
        r = client.patch(f"{API}/entries/{note}?chat_id={chat}",
                         json={"text": "Mira keeps\u200b the\u2060 mill."})
        assert r.status_code == 200, r.text
        # POSITIVE CONTROL: the new text was stored, invisible marks and all.
        with database.get_db() as con:
            stored = con.execute("SELECT text FROM notebook_entries "
                                 "WHERE id = ?", (note,)).fetchone()[0]
        assert "\u200b" in stored
        assert _marks(note)[0] is None

    @pytest.mark.parametrize("words", [
        "Mira keeps the mills.",           # one changed word
        "mira keeps the mill.",            # letter case only
        "Mira keeps the mill",             # punctuation only
    ])
    def test_any_other_change_is_an_edit(self, client, notes, words) -> None:
        """Twin of the rows above: case, punctuation and letters count."""
        chat, note, _ = notes
        r = client.patch(f"{API}/entries/{note}?chat_id={chat}",
                         json={"text": words})
        assert r.status_code == 200, r.text
        assert _marks(note)[0] is not None

    @pytest.mark.parametrize("body", [{"pinned": True}, {"kind": "event"},
                                      {"importance": 3},
                                      {"durability": "scene"}])
    def test_a_flag_change_is_not_an_edit(self, client, notes, body) -> None:
        chat, note, _ = notes
        r = client.patch(f"{API}/entries/{note}?chat_id={chat}", json=body)
        assert r.status_code == 200, r.text
        assert _marks(note) == (None, None)
        # Positive control: the flag DID change, so this was not a no-op.
        with database.get_db() as con:
            row = con.execute("SELECT * FROM notebook_entries WHERE id = ?",
                              (note,)).fetchone()
        key, value = next(iter(body.items()))
        assert row[key] == (1 if value is True else value)

    def test_a_reorder_is_not_an_edit(self, client, notes) -> None:
        chat, a, b = notes
        r = client.post(f"{API}/{chat}/reorder", json={"ordered_ids": [b, a]})
        assert r.status_code == 200, r.text
        assert _marks(a) == (None, None) and _marks(b) == (None, None)

    def test_an_accept_is_not_an_edit(self, client, notes) -> None:
        chat, _, proposed = notes
        r = client.post(f"{API}/entries/{proposed}/accept?chat_id={chat}")
        assert r.status_code == 200, r.text
        assert _marks(proposed)[0] is None

    def test_a_later_flag_change_keeps_the_edit(self, client, notes) -> None:
        chat, note, _ = notes
        client.patch(f"{API}/entries/{note}?chat_id={chat}",
                     json={"text": "Mira sold the mill."})
        first = _marks(note)[0]
        client.patch(f"{API}/entries/{note}?chat_id={chat}",
                     json={"pinned": True})
        assert _marks(note)[0] == first


class TestAcceptedByHand:
    def test_keep_on_a_proposal_sets_it(self, client, notes) -> None:
        chat, _, proposed = notes
        assert _marks(proposed) == (None, None)        # ground
        r = client.post(f"{API}/entries/{proposed}/accept?chat_id={chat}")
        assert r.status_code == 200, r.text
        assert _marks(proposed)[1] is not None
        assert r.json()["hand_accepted_at"] is not None

    def test_keep_pressed_again_does_not_restamp(self, client, notes) -> None:
        chat, _, proposed = notes
        r = client.post(f"{API}/entries/{proposed}/accept?chat_id={chat}")
        assert r.status_code == 200, r.text
        with database.get_db() as con:
            con.execute("UPDATE notebook_entries SET hand_accepted_at = "
                        "'2000-01-01 00:00:00' WHERE id = ?", (proposed,))
        r = client.post(f"{API}/entries/{proposed}/accept?chat_id={chat}")
        assert r.status_code == 200, r.text
        assert _marks(proposed)[1] == "2000-01-01 00:00:00"

    def test_an_accepted_note_is_never_marked_by_later_presses(
            self, client, notes):
        """An accept, an edit and a pin on a note that was already accepted:
        it is still not something the reader Kept."""
        chat, note, _ = notes
        r = client.post(f"{API}/entries/{note}/accept?chat_id={chat}")
        assert r.status_code == 200, r.text
        r = client.patch(f"{API}/entries/{note}?chat_id={chat}",
                         json={"text": "Changed.", "pinned": True})
        assert r.status_code == 200, r.text
        assert _marks(note)[1] is None
        assert _marks(note)[0] is not None             # positive control

    def test_an_auto_accepted_note_is_never_marked(self, client) -> None:
        """Through the real writer: `commit_extraction` with the automatic
        switch on inserts the note as accepted, and nobody Kept it."""
        with database.get_db() as con:
            chat = _chat(con)
            for i in range(4):
                con.execute("INSERT INTO messages (chat_id, role, content) "
                            "VALUES (?, ?, ?)",
                            (chat, "user" if i % 2 == 0 else "assistant",
                             f"line {i}: her brother owns the mill"))
        with database.get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            lo, hi = con.execute("SELECT MIN(id), MAX(id) FROM messages "
                                 "WHERE chat_id = ?", (chat,)).fetchone()
            notebook.commit_extraction(
                con, work_key="auto-accept-mark", chat_id=chat,
                from_id=lo, to_id=hi, proposals=[{
                    "text": "Her brother owns the mill.",
                    "evidence": "her brother owns the mill",
                    "kind": "fact", "durability": "permanent",
                    "importance": 2, "supersedes": None}])
        with database.get_db() as con:
            rows = con.execute(
                "SELECT status, provenance, hand_accepted_at, edited_at "
                "FROM notebook_entries WHERE chat_id = ?", (chat,)).fetchall()
        # POSITIVE CONTROL: it landed, as the model's, and accepted.
        assert [(r["status"], r["provenance"]) for r in rows] == [
            (notebook.STATUS_ACCEPTED, notebook.PROV_MODEL)]
        assert rows[0]["hand_accepted_at"] is None
        assert rows[0]["edited_at"] is None

    def test_editing_a_proposal_is_not_keeping_it(self, client, notes) -> None:
        chat, _, proposed = notes
        r = client.patch(f"{API}/entries/{proposed}?chat_id={chat}",
                         json={"text": "Demir owns two boats."})
        assert r.status_code == 200, r.text
        edited, accepted = _marks(proposed)
        assert edited is not None                      # positive control
        assert accepted is None, "an edit of a proposal was taken as a Keep"
        assert r.json()["status"] == notebook.STATUS_PROPOSED

    def test_both_marks_reach_the_panel(self, client, notes) -> None:
        chat, note, proposed = notes
        client.patch(f"{API}/entries/{note}?chat_id={chat}",
                     json={"text": "Mira sold the mill."})
        client.post(f"{API}/entries/{proposed}/accept?chat_id={chat}")
        listed = {e["id"]: e for e in
                  client.get(f"{API}/{chat}").json()["entries"]}
        assert listed[note]["edited_at"] is not None
        assert listed[note]["hand_accepted_at"] is None
        assert listed[proposed]["hand_accepted_at"] is not None
        assert listed[proposed]["edited_at"] is None


# ---------------------------------------------------------------------------
# the two tables: rules, lifetime, digest
# ---------------------------------------------------------------------------

class TestTheChunkReadRules:
    def _insert(self, con, chat, **over):
        row = {"chunk_no": 1, "from_message_id": 1, "to_message_id": 20,
               "state": "current", "changed_at": None}
        row.update(over)
        con.execute(
            "INSERT INTO notebook_chunk_reads (chat_id, chunk_no, "
            "from_message_id, to_message_id, state, changed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat, row["chunk_no"], row["from_message_id"],
             row["to_message_id"], row["state"], row["changed_at"]))

    def test_the_two_honest_states_are_accepted(self, db) -> None:
        with database.get_db() as con:
            chat = _chat(con)
            self._insert(con, chat)
            self._insert(con, chat, state="changed",
                         changed_at="2026-09-27 10:00:00")
            assert con.execute("SELECT COUNT(*) FROM notebook_chunk_reads"
                               ).fetchone()[0] == 2

    @pytest.mark.parametrize("over", [
        {"state": "changed"},                                # no evidence
        {"changed_at": "2026-09-27 10:00:00"},               # no word
        {"state": "gone", "changed_at": "2026-09-27 10:00:00"},
        {"from_message_id": 21, "to_message_id": 20},
        {"chunk_no": 0},
    ])
    def test_a_contradiction_is_refused(self, db, over) -> None:
        with database.get_db() as con:
            chat = _chat(con)
        with pytest.raises(database.sqlite3.IntegrityError):
            with database.get_db() as con:
                self._insert(con, chat, **over)


class TestTheyGoWithTheChat:
    def test_deleting_a_chat_takes_both_and_spares_the_other(
            self, client) -> None:
        with database.get_db() as con:
            doomed, kept = _chat(con), _chat(con)
            for chat in (doomed, kept):
                con.execute(
                    "INSERT INTO notebook_chunk_reads (chat_id, chunk_no, "
                    "from_message_id, to_message_id) VALUES (?, 1, 1, 20)",
                    (chat,))
                con.execute(
                    "INSERT INTO notebook_deleted_traces (chat_id, digest) "
                    "VALUES (?, ?)", (chat, notebook.deleted_note_digest(
                        con, f"note of {chat}")))
        r = client.delete(f"/api/v1/chats/{doomed}")
        assert r.status_code == 200, r.text
        with database.get_db() as con:
            for table in database._RANGE_READING_TABLES:
                chats = [x[0] for x in con.execute(
                    f"SELECT chat_id FROM {table}").fetchall()]
                assert chats == [kept], (table, chats)


class TestABuildThatDoesNotKnowThemCanStillDeleteTheChat:
    """ON DELETE CASCADE on both tables' `chat_id`: an older 6-build deletes
    a chat with its own list of tables and then `DELETE FROM chats`, and a
    row in a table it has never heard of must not refuse that."""

    def test_a_bare_chat_delete_takes_both_tables_rows(self, db) -> None:
        with database.get_db() as con:
            # GROUND: the connection enforces foreign keys, so a key without
            # a cascade would refuse the delete below.
            assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            doomed, kept = _chat(con), _chat(con)
            for chat in (doomed, kept):
                con.execute(
                    "INSERT INTO notebook_chunk_reads (chat_id, chunk_no, "
                    "from_message_id, to_message_id) VALUES (?, 1, 1, 20)",
                    (chat,))
                con.execute(
                    "INSERT INTO notebook_deleted_traces (chat_id, digest) "
                    "VALUES (?, ?)", (chat, notebook.deleted_note_digest(
                        con, f"note of {chat}")))
        with database.get_db() as con:
            con.execute("DELETE FROM chats WHERE id = ?", (doomed,))
        with database.get_db() as con:
            for table in database._RANGE_READING_TABLES:
                chats = [x[0] for x in con.execute(
                    f"SELECT chat_id FROM {table}").fetchall()]
                assert chats == [kept], (table, chats)


class TestTheDeletedNoteDigest:
    def test_a_line_break_matches_the_stored_note(self, db) -> None:
        """Normalised exactly as the duplicate check normalises, `_flat`
        included: a stored note is flat ("a / b"), a raw model line is not."""
        raw = "Demir owns the mill.\nHis sister keeps the key."
        stored = notebook._flat(raw)
        assert stored != raw                            # ground: they differ
        with database.get_db() as con:
            assert notebook.deleted_note_digest(con, raw) == \
                notebook.deleted_note_digest(con, stored)
            # POSITIVE CONTROL: the same words joined by a plain space are a
            # different note to the duplicate check, and so here.
            assert notebook.deleted_note_digest(con, raw) != \
                notebook.deleted_note_digest(
                    con, "Demir owns the mill. His sister keeps the key.")

    def test_two_first_uses_get_the_same_key(self, db, monkeypatch) -> None:
        """Both callers find no key (the race), both mint; the first insert
        wins and both digest under it. Driven as two connections, each made
        to see the key missing on its first read."""
        real = secrets_service.get_secret
        missed: set[int] = set()

        def racing(name, *, conn=None):
            if name == notebook.TRACE_KEY_SECRET and id(conn) not in missed:
                missed.add(id(conn))
                return None                             # "not there yet"
            return real(name, conn=conn)

        monkeypatch.setattr(secrets_service, "get_secret", racing)
        text = "Demir received the mill."
        with database.get_db() as con:
            first = notebook.deleted_note_digest(con, text)
        with database.get_db() as con:
            second = notebook.deleted_note_digest(con, text)
        # GROUND: both calls really took the mint path.
        assert len(missed) == 2
        assert first == second, "the second first-use replaced the key"

    def test_the_same_sentence_matches_through_case_and_spacing(self, db):
        with database.get_db() as con:
            a = notebook.deleted_note_digest(con, "Demir received the mill.")
            b = notebook.deleted_note_digest(con, "  demir   RECEIVED the mill")
            c = notebook.deleted_note_digest(con, "Demir received the boat.")
        assert a == b
        assert a != c, "positive control: a different sentence differs"
        assert len(a) == 64

    def test_it_is_keyed_by_this_vault(self, db) -> None:
        text = "Demir received the mill."
        with database.get_db() as con:
            first = notebook.deleted_note_digest(con, text)
            key = secrets_service.get_secret(notebook.TRACE_KEY_SECRET,
                                             conn=con)
        assert key and len(bytes.fromhex(key)) == 32
        # Not a bare hash: anyone with the file and a guess could check that.
        plain = hashlib.sha256(
            notebook._dedup_key(text).encode("utf-8")).hexdigest()
        assert first != plain
        # Another vault's key gives another digest for the same sentence.
        with database.get_db() as con:
            secrets_service.delete_secret(notebook.TRACE_KEY_SECRET, conn=con)
            second = notebook.deleted_note_digest(con, text)
        assert second != first
        # And the key is stable within one vault.
        with database.get_db() as con:
            assert notebook.deleted_note_digest(con, text) == second

    def test_the_table_holds_the_digest_and_refuses_anything_else(
            self, db) -> None:
        with database.get_db() as con:
            chat = _chat(con)
            d = notebook.deleted_note_digest(con, "Mira keeps the mill.")
            con.execute("INSERT INTO notebook_deleted_traces (chat_id, digest)"
                        " VALUES (?, ?)", (chat, d))
        with pytest.raises(database.sqlite3.IntegrityError):
            with database.get_db() as con:     # the same sentence twice
                con.execute("INSERT INTO notebook_deleted_traces "
                            "(chat_id, digest) VALUES (?, ?)", (chat, d))
        with pytest.raises(database.sqlite3.IntegrityError):
            with database.get_db() as con:     # plaintext does not fit
                con.execute("INSERT INTO notebook_deleted_traces "
                            "(chat_id, digest) VALUES (?, ?)",
                            (chat, "Mira keeps the mill."))
        sentence = "Mira keeps the mill and Demir keeps the boat, all winter."
        sentence = (sentence * 2)[:64]
        assert len(sentence) == 64                      # ground: length fits
        with pytest.raises(database.sqlite3.IntegrityError):
            with database.get_db() as con:     # nor does a 64-char sentence
                con.execute("INSERT INTO notebook_deleted_traces "
                            "(chat_id, digest) VALUES (?, ?)",
                            (chat, sentence))
