"""test_migration_atomicity.py - pins _migrate_notebook's atomicity.

database.py:265-271 promises _migrate_notebook is all-or-nothing. That promise
holds only because an unconditional UPDATE earlier in _migrate() opens the
implicit transaction every later DDL statement rides inside - Python's legacy
sqlite3 transaction control opens a transaction before DML but never before
bare DDL, so DDL with nothing open beforehand autocommits per statement. Move
or delete that UPDATE and the notebook tables would autocommit one at a time,
and a crash partway through would leave some of them behind permanently.

Nothing tested the ordering before this file. This drives a real populated
vault through a real crash injected inside the real _migrate_notebook call and
checks what survives - not a scan of database.py's source.
"""
from __future__ import annotations

import pytest

import database
import vault_state
from tests.conftest import TEST_VAULT_KEY

_OLD_MESSAGE = "a message from before the notebook feature existed"
_OLD_CHARACTER = "Atomicity Test Character"

#: Where the crash lands - inside _migrate_notebook, AFTER notebook_entries
#: is created and BEFORE boundaries is. Landing here proves the strong claim:
#: not just "boundaries never appears", but "notebook_entries disappears too,
#: even though its own CREATE already succeeded" - which is only possible if
#: both statements share one transaction.
_CRASH_ON = "CREATE TABLE IF NOT EXISTS boundaries"


def _seed_pre_notebook_vault(db_path: str, key: bytes) -> None:
    """A populated vault as it looked before the notebook feature shipped:
    only the CREATE-only base schema, none of _migrate's ALTERs or tables.
    Real user vaults reaching this migration for the first time look exactly
    like this."""
    con = database.sqlite3.connect(db_path)
    try:
        database._key_pragma(con, key)
        con.executescript(database._SCHEMA)
        con.execute(
            "INSERT INTO characters (id, name, first_mes) VALUES (1, ?, 'hi')",
            (_OLD_CHARACTER,),
        )
        con.execute(
            "INSERT INTO chats (id, character_id, title) VALUES (1, 1, 'Old chat')"
        )
        con.execute(
            "INSERT INTO messages (id, chat_id, role, content) "
            "VALUES (1, 1, 'user', ?)",
            (_OLD_MESSAGE,),
        )
        con.commit()
    finally:
        con.close()


class _ExplodingConnection:
    """Wraps a real connection; raises the moment the chosen statement runs.

    Stands in for a real interruption landing mid-migration - a killed
    process, a power cut, an OS hiccup. What happens to everything the
    migration already did before that moment is exactly what this file
    checks.
    """

    def __init__(self, real, trigger: str):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_trigger", trigger)

    def execute(self, sql, *args, **kwargs):
        if self._trigger in sql:
            raise RuntimeError("simulated crash mid notebook migration")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)


def _inject_crash(db_path: str) -> pytest.MonkeyPatch:
    """Make the NEXT connect() to db_path explode on _CRASH_ON, and every
    other connect() (there are none in this file, but future callers may add
    one) behave normally.

    Returns its OWN MonkeyPatch, deliberately not the test's fixture-provided
    one. The `vault` fixture and the test function both depend on
    `monkeypatch`, and pytest hands them the SAME instance for one test node -
    so undo()ing that shared instance to restore sqlite3.connect would also
    undo the fixture's DB_PATH redirection, and the "retry" that follows would
    silently open a different file and call THAT corruption. Its own instance
    means undo() reverts exactly the one thing this function patched.
    """
    real_connect = database.sqlite3.connect

    def exploding_connect(path, *args, **kwargs):
        real = real_connect(path, *args, **kwargs)
        if path == db_path:
            return _ExplodingConnection(real, _CRASH_ON)
        return real

    mp = pytest.MonkeyPatch()
    mp.setattr(database.sqlite3, "connect", exploding_connect)
    return mp


def _reconnect(db_path: str, key: bytes):
    con = database.sqlite3.connect(db_path)
    database._key_pragma(con, key)
    return con


def _table_exists(con, name: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _column_exists(con, table: str, column: str) -> bool:
    return any(r[1] == column
              for r in con.execute(f"PRAGMA table_info({table})").fetchall())


@pytest.fixture()
def vault(tmp_path, monkeypatch, request):
    """A populated, NOT-yet-migrated vault - one step before the `db` fixture
    elsewhere in this suite, which always runs init_db() to completion. This
    file exists to interrupt that exact step, so it has to start before it.
    """
    import config

    db_path = str(tmp_path / "test_app.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    _seed_pre_notebook_vault(db_path, TEST_VAULT_KEY)
    request.addfinalizer(vault_state.clear_key)
    vault_state.set_key(TEST_VAULT_KEY)
    return db_path


class TestAnUninterruptedMigrationReallyDoesSomething:
    """The ground. Without this, "nothing changed after the forced failure"
    would pass just as well with _migrate_notebook deleted outright - the
    tests below would be proving atomicity for a migration that never ran."""

    def test_the_notebook_tables_and_the_backfill_actually_land(self, vault
                                                                 ) -> None:
        database.init_db()
        con = _reconnect(vault, TEST_VAULT_KEY)
        try:
            assert _table_exists(con, "notebook_entries")
            assert _table_exists(con, "boundaries")
            assert _column_exists(con, "messages", "truncated")
            row = con.execute(
                "SELECT updated_at FROM messages WHERE id = 1").fetchone()
            assert row[0] is not None, "the updated_at backfill never ran"
            version = con.execute("PRAGMA user_version").fetchone()[0]
            assert version == database._SCHEMA_VERSION
        finally:
            con.close()


class TestACrashMidMigrationTakesEverythingAfterTheUpdateWithIt:
    def test_notebook_entries_is_gone_too_even_though_its_own_create_ran(
        self, vault
    ) -> None:
        """notebook_entries is created strictly BEFORE boundaries inside
        _migrate_notebook. If the two rode in separate (autocommitting)
        transactions, killing the process while creating boundaries would
        leave notebook_entries behind - a vault with one notebook table and
        not the other, and nothing on the next boot to say anything went
        wrong. They must fall together or the atomicity claim is false."""
        crash = _inject_crash(vault)
        try:
            with pytest.raises(RuntimeError, match="simulated crash"):
                database.init_db()
        finally:
            crash.undo()             # real sqlite3.connect back for recovery

        con = _reconnect(vault, TEST_VAULT_KEY)
        try:
            assert not _table_exists(con, "notebook_entries"), (
                "notebook_entries survived a crash that happened creating "
                "boundaries, the table right after it - the migration is "
                "not atomic")
            assert not _table_exists(con, "boundaries")
            # The same transaction also carries the "truncated" ALTER and the
            # user_version bump, both of which run between the UPDATE and
            # the notebook tables - they must fall back too.
            assert not _column_exists(con, "messages", "truncated")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            assert version < database._SCHEMA_VERSION, (
                "user_version advanced despite the migration failing partway")
            # Unchanged, not just "the new tables are gone": what was already
            # in the vault before this boot has to still be exactly that.
            row = con.execute(
                "SELECT name FROM characters WHERE id = 1").fetchone()
            assert row[0] == _OLD_CHARACTER
            row = con.execute(
                "SELECT content FROM messages WHERE id = 1").fetchone()
            assert row[0] == _OLD_MESSAGE
        finally:
            con.close()

    def test_the_vault_still_opens_on_the_next_launch(self, vault) -> None:
        """A migration that dies partway must not brick the vault. The next
        launch has to pick the whole thing up again from the untouched
        pre-migration state the test above just proved survives - not fail
        forever on a database stuck half-upgraded."""
        crash = _inject_crash(vault)
        try:
            with pytest.raises(RuntimeError, match="simulated crash"):
                database.init_db()
        finally:
            crash.undo()

        database.init_db()          # the real retry a relaunch performs

        con = _reconnect(vault, TEST_VAULT_KEY)
        try:
            assert _table_exists(con, "notebook_entries")
            assert _table_exists(con, "boundaries")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            assert version == database._SCHEMA_VERSION
            row = con.execute(
                "SELECT content FROM messages WHERE id = 1").fetchone()
            assert row[0] == _OLD_MESSAGE
        finally:
            con.close()
