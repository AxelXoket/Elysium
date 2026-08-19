"""FAZ 1 - the notebook's tables, and the migration that installs them.

Everything here is about the moment a database changes shape. That moment has
exactly two honest outcomes - the new shape and its version stamp both land, or
neither does - and the traps that make a third outcome possible are all
documented rather than hypothetical:

  * `executescript()` commits before every statement, so DDL routed through
    _SCHEMA can never share a transaction with the stamp that records it;
  * `CREATE INDEX IF NOT EXISTS` is a no-op when the NAME exists, whatever the
    definition says, so an index that changes shape later silently keeps the
    old one on every machine that already ran it;
  * `PRAGMA integrity_check` does not look at foreign keys at all;
  * and a unique index built over data that already violates it aborts the
    boot, every boot, with no way back in.

So these tests do not check that the tables exist. They check that the ways
this could go wrong quietly are closed.
"""
from __future__ import annotations

import pytest

import database
from database import sqlite3          # the SQLCipher driver, not stdlib


def _a_chat(con) -> int:
    """chats.character_id is NOT NULL, so a chat needs a character first."""
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    cid = con.execute("SELECT MAX(id) FROM characters").fetchone()[0]
    con.execute("INSERT INTO chats (character_id, title) VALUES (?, 't')", (cid,))
    return con.execute("SELECT MAX(id) FROM chats").fetchone()[0]


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


class TestTheShapeArrives:
    def test_the_four_tables_and_the_two_columns(self, db) -> None:
        with database.get_db() as con:
            assert {"notebook_entries", "boundaries", "notebook_extractions",
                    "notebook_spend"} <= _tables(con)
            chat_cols = {r[1] for r in
                         con.execute("PRAGMA table_info(chats)").fetchall()}
            assert "use_global_boundaries" in chat_cols
            assert "notebook_auto_accept_override" in chat_cols

    def test_a_chat_follows_the_global_boundaries_unless_told_otherwise(self, db):
        """Defaults are a promise too: a chat nobody configured still gets the
        limits its owner wrote once."""
        with database.get_db() as con:
            _a_chat(con)
            row = con.execute(
                "SELECT use_global_boundaries, notebook_auto_accept_override "
                "FROM chats ORDER BY id DESC LIMIT 1").fetchone()
        assert row[0] == 1
        assert row[1] is None       # NULL = follow the app-wide switch

    def test_the_stamp_matches_what_this_build_understands(self, db) -> None:
        with database.get_db() as con:
            assert con.execute("PRAGMA user_version").fetchone()[0] == \
                database._SCHEMA_VERSION

    def test_running_it_again_changes_nothing(self, db) -> None:
        """Every unlock runs this. Idempotence is not a nicety here."""
        with database.get_db() as con:
            chat = _a_chat(con)
            con.execute(
                "INSERT INTO notebook_entries (chat_id, position, text) "
                "VALUES (?, 0, 'survives')", (chat,))
        database.init_db()
        with database.get_db() as con:
            assert con.execute(
                "SELECT text FROM notebook_entries").fetchone()[0] == "survives"


class TestTheEngineRefusesWhatCodeCouldForget:
    """These are CHECK constraints, not helper functions, and that is the point:
    a rule enforced in one write path is a rule the next write path can skip."""

    def test_an_inferred_boundary_can_never_be_hard(self, db) -> None:
        with database.get_db() as con, pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO boundaries (scope, label, phrasing, severity, source)"
                " VALUES ('global', 'l', 'p', 'hard', 'inferred')")

    def test_an_inferred_boundary_may_still_be_soft(self, db) -> None:
        """The positive control. A guard that refuses everything is not a guard,
        and inference is allowed to suggest - it is only forbidden to bind."""
        with database.get_db() as con:
            con.execute(
                "INSERT INTO boundaries (scope, label, phrasing, severity, source)"
                " VALUES ('global', 'l', 'p', 'soft', 'inferred')")

    @pytest.mark.parametrize("scope,chat", [("global", 1), ("chat", None)])
    def test_scope_and_owner_must_say_the_same_thing(self, db, scope, chat):
        with database.get_db() as con, pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO boundaries (scope, chat_id, label, phrasing, severity)"
                " VALUES (?, ?, 'l', 'p', 'soft')", (scope, chat))

    def test_a_kind_outside_the_taxonomy_is_refused(self, db) -> None:
        with database.get_db() as con:
            chat = _a_chat(con)
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO notebook_entries (chat_id, position, text, kind)"
                    " VALUES (?, 0, 'x', 'invented')", (chat,))


class TestTheMigrationSurvivesADamagedDatabase:
    """A database that arrived from somewhere else - an older build, a restored
    premigrate backup - is the case where a migration either heals or bricks."""

    def test_rows_whose_chat_is_gone_are_swept(self, db) -> None:
        """Foreign keys are NOT enforced on init_db's connection, so nothing
        stopped these arriving. `integrity_check` would not find them either."""
        # get_db() turns foreign_keys ON, and these rows are exactly the ones
        # that arrive when it was OFF - a restored premigrate backup, an older
        # build. Planted the way they really appear.
        con = sqlite3.connect(database.DB_PATH)
        try:
            database._key_pragma(con, database.get_key())
            con.execute(
                "INSERT INTO notebook_entries (chat_id, position, text) "
                "VALUES (98765, 0, 'orphan')")
            con.execute(
                "INSERT INTO boundaries (scope, chat_id, label, phrasing, severity)"
                " VALUES ('chat', 98765, 'l', 'p', 'soft')")
            con.commit()
        finally:
            con.close()
        database.init_db()
        with database.get_db() as con:
            assert con.execute(
                "SELECT COUNT(*) FROM notebook_entries").fetchone()[0] == 0
            assert con.execute(
                "SELECT COUNT(*) FROM boundaries").fetchone()[0] == 0
            assert con.execute("PRAGMA foreign_key_check").fetchall() == []

    def test_a_global_boundary_is_not_swept(self, db) -> None:
        """The sweep works by chat, and a global limit belongs to no chat. If
        this ever goes red, every limit the owner wrote disappears on unlock."""
        with database.get_db() as con:
            con.execute(
                "INSERT INTO boundaries (scope, label, phrasing, severity) "
                "VALUES ('global', 'no gore', 'Avoid graphic injury.', 'hard')")
        database.init_db()
        with database.get_db() as con:
            assert con.execute(
                "SELECT COUNT(*) FROM boundaries").fetchone()[0] == 1

    def test_duplicate_positions_are_renumbered_not_deleted(self, db) -> None:
        """The unique index cannot be built over them, and deleting the loser
        would break the owner's rule that a note never disappears. So they move.
        """
        with database.get_db() as con:
            chat = _a_chat(con)
            con.execute("DROP INDEX IF EXISTS idx_notebook_order_v1")
            for text in ("first", "collides"):
                con.execute(
                    "INSERT INTO notebook_entries (chat_id, position, text) "
                    "VALUES (?, 7, ?)", (chat, text))

        database.init_db()          # must not raise

        with database.get_db() as con:
            rows = dict(con.execute(
                "SELECT text, position FROM notebook_entries").fetchall())
        assert set(rows) == {"first", "collides"}, "a note was deleted"
        assert rows["first"] == 7
        assert rows["collides"] != 7, "the collision was not resolved"


class TestADatabaseFromTheFuture:
    def test_it_refuses_rather_than_editing(self, db, monkeypatch) -> None:
        """SQLite has no protection of its own: an older build would happily
        write rows the newer schema forbids, and nothing would notice until the
        newer build came back to a database it could no longer trust.

        Refusing fails the unlock. That is the intended direction - loudly shut
        beats quietly wrong - and the message has to name what happened.
        """
        with database.get_db() as con:
            con.execute(
                f"PRAGMA user_version = {database._SCHEMA_VERSION + 5}")

        with pytest.raises(RuntimeError) as exc:
            database.init_db()

        said = str(exc.value)
        assert "newer version" in said
        assert str(database._SCHEMA_VERSION + 5) in said
        assert "Nothing was changed" in said

    def test_the_same_version_is_not_the_future(self, db) -> None:
        """Positive control - the guard is `>`, not `>=`. Getting that wrong
        makes every ordinary unlock fail."""
        with database.get_db() as con:
            con.execute(f"PRAGMA user_version = {database._SCHEMA_VERSION}")
        database.init_db()


class TestPlaintextNeverReachesATempFile:
    """Found in FAZ 1, and it predates the notebook.

    SQLCipher encrypts the database, the journal and the WAL. It does NOT
    encrypt SQLite's temporary store, and its own design note names disabling
    the file-based one as a required step. This app had never set it: an
    ORDER BY that spills, a materialised subquery or a VACUUM would write
    plaintext rows into a temp file sitting next to the encrypted database.

    The notebook is what made it urgent - its rows are the most distilled text
    the app holds - but the hole was open for everything already stored.
    """

    def test_every_connection_keeps_its_scratch_space_in_ram(self, db) -> None:
        with database.get_db() as con:
            # 0 = default(file) · 1 = FILE · 2 = MEMORY
            assert con.execute("PRAGMA temp_store").fetchone()[0] == 2

    def test_the_backup_path_gets_it_too(self, db, tmp_path) -> None:
        """A backup opens its own connection. It copies the same pages and
        deserves the same rule; the funnel is _key_pragma precisely so no
        caller has to remember."""
        dest = tmp_path / "copy.db"
        database.backup_encrypted(str(dest))
        con = sqlite3.connect(str(dest))
        try:
            database._key_pragma(con, database.get_key())
            assert con.execute("PRAGMA temp_store").fetchone()[0] == 2
        finally:
            con.close()
