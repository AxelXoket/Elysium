"""database.py - Raw sqlite3-API helpers over SQLCipher. No ORM.

The engine is SQLCipher (sqlcipher3-wheels), whose dbapi2 is a drop-in fork
of stdlib sqlite3 - every caller keeps its `dict(row)` / `.fetchone()` /
`executescript` idioms untouched. The WHOLE file (pages, WAL, -shm) is
AES-256 encrypted at rest.

Keying model (docs/ENCRYPTION_PLAN.md): scrypt runs ONCE per unlock in
crypto.py; connections receive the RAW 32-byte key via
`PRAGMA key = "x'<hex>'"`. Raw form on purpose - the passphrase form would
run SQLCipher's internal PBKDF2 (256k iterations) on EVERY connection, and
this app opens a connection per request.

Public API:
    init_db()          - idempotent schema bootstrap; requires unlocked vault.
    get_db()           - context manager yielding a keyed connection;
                         raises VaultLockedError while locked.
    check_key()        - does this key open the current DB file?
    rekey_db()         - re-encrypt the DB under a new key (passphrase change).
    is_plaintext_db()  - pre-vault app.db detection (migration).
    migrate_plaintext_to_encrypted() - one-shot plaintext → encrypted move.
    get/set/delete_setting()         - settings rows.

Rules:
- WAL mode is set before any DDL (after keying).
- row_factory=sqlite3.Row so callers can use dict(row) on any result.
- Commit on clean exit, rollback on exception, always close.
- Message order is always enforced by the caller with ORDER BY id ASC.
- The key is NEVER logged; pragma statements are built without logging.
"""

import contextlib
import logging
import os
import time
from pathlib import Path
from typing import Generator

from sqlcipher3 import dbapi2 as sqlite3

import secure_delete
from config import DB_PATH
from vault_state import VaultLockedError, get_key  # noqa: F401 - re-exported

logger = logging.getLogger(__name__)


def _key_pragma(con: "sqlite3.Connection", key: bytes, *, rekey: bool = False) -> None:
    """Apply the raw-key PRAGMA. Hex comes from bytes.hex() (charset [0-9a-f])
    so string interpolation is injection-safe; PRAGMA takes no parameters."""
    stmt = "rekey" if rekey else "key"
    con.execute(f"PRAGMA {stmt} = \"x'{key.hex()}'\"")
    # Deleted rows are overwritten rather than left on the freelist. Without
    # this a note the user deleted stays verbatim in its page until something
    # reuses it - readable by anyone holding the passphrase, which is exactly
    # the audience "delete" is meant to exclude. It matters more than usual
    # here because full copies of this database exist under the SAME key (the
    # premigrate and rekey sidecars), so residue outlives a rotation.
    con.execute("PRAGMA secure_delete = ON")
    # Temporary tables and sort spills stay in RAM. SQLCipher's own design note
    # names disabling the file-based temp store as a required step and it was
    # missing: with the default, a spilling ORDER BY, a materialised subquery
    # or a VACUUM writes PLAINTEXT rows into a temp file beside the encrypted
    # database. Applied on the same funnel as the key so no connection can open
    # without it - except the unkeyed source in migrate_plaintext_to_encrypted,
    # which never passes through here and is handled at its own site.
    con.execute("PRAGMA temp_store = MEMORY")
    # Temporary tables and sort spills stay in RAM. SQLCipher's own design note
    # calls this out as a required step and it was missing: with the default
    # file-backed temp store, an ORDER BY that spills, a subquery that
    # materialises, or a VACUUM writes PLAINTEXT rows into a temp file next to
    # the encrypted database - outside the file every promise in this module is
    # about. Applied on the same funnel as the key so no connection can be
    # opened without it, including the backup and rekey paths.
    con.execute("PRAGMA temp_store = MEMORY")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """\
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    name                     TEXT NOT NULL,
    description              TEXT NOT NULL DEFAULT '',
    personality              TEXT NOT NULL DEFAULT '',
    scenario                 TEXT NOT NULL DEFAULT '',
    first_mes                TEXT NOT NULL DEFAULT '',
    mes_example              TEXT NOT NULL DEFAULT '',
    system_prompt            TEXT NOT NULL DEFAULT '',
    post_history_instruction TEXT NOT NULL DEFAULT '',
    tags                     TEXT NOT NULL DEFAULT '[]',
    raw_json                 TEXT NOT NULL DEFAULT '{}',
    created_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id),
    title        TEXT,
    model_id     TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL REFERENCES chats(id),
    role       TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);

CREATE TABLE IF NOT EXISTS personas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS attachments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER REFERENCES messages(id),  -- NULL while staged (uploaded, not yet sent)
    sha256     TEXT    NOT NULL,
    mime       TEXT    NOT NULL,
    width      INTEGER NOT NULL,
    height     INTEGER NOT NULL,
    byte_size  INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_attachments_message_id ON attachments(message_id);

-- E6: image bytes live INSIDE the encrypted DB (content-addressed, shared by
-- every attachments row with the same sha256). No plaintext image ever
-- touches the filesystem; SQLCipher covers at-rest, rekey, and lock for free.
-- byte_size on attachments stays the plaintext length of this data.
CREATE TABLE IF NOT EXISTS attachment_blobs (
    sha256 TEXT PRIMARY KEY,
    data   BLOB NOT NULL
);

-- E5: app secrets (OpenRouter API key, proxy URL) sealed in the vault.
-- Row names intentionally equal the legacy OS-keyring usernames so the
-- one-time migration maps 1:1. Values are never logged.
CREATE TABLE IF NOT EXISTS vault_secrets (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def _migrate(con: sqlite3.Connection) -> None:
    """Idempotent column/index additions on top of the CREATE-only schema.

    _SCHEMA can only CREATE IF NOT EXISTS; existing databases need ALTERs
    guarded by PRAGMA table_info (an unconditional ALTER crashes the second
    boot). ADD COLUMN ... DEFAULT backfills existing rows, so every
    pre-migration message becomes its own active singleton.
    """
    # DOWNGRADE GUARD, and it runs before a single DDL statement. SQLite has no
    # protection of its own: an older build opening a newer file will happily
    # write rows that the newer schema's constraints would have refused, and
    # nothing notices until the newer build comes back to a database it can no
    # longer trust. Refusing is louder than repairing, and this is the only
    # moment where refusing is still free.
    #
    # It fails the unlock, deliberately. `init_db` has no error path, so this
    # propagates out of _bootstrap_unlocked and the vault does not open - which
    # is the correct direction: a database written by a future build is not
    # something this build should be quietly editing.
    on_disk = con.execute("PRAGMA user_version").fetchone()[0]
    if on_disk > _SCHEMA_VERSION:
        raise RuntimeError(
            f"This database was written by a newer version of Elysium "
            f"(schema {on_disk}, this build understands {_SCHEMA_VERSION}). "
            f"Nothing was changed. Use the newer version, or restore a backup "
            f"taken with this one."
        )

    cols = {r[1] for r in con.execute("PRAGMA table_info(messages)").fetchall()}
    if "variant_group" not in cols:
        # NULL = never regenerated; else the id of the group's FIRST row (the
        # anchor), so COALESCE(variant_group, id) is any row's group key.
        con.execute("ALTER TABLE messages ADD COLUMN variant_group INTEGER")
    if "active" not in cols:
        con.execute(
            "ALTER TABLE messages ADD COLUMN active INTEGER NOT NULL DEFAULT 1"
        )
    if "updated_at" not in cols:
        # v1.1 (I6/I13): row-level change stamp for edit optimistic
        # concurrency. ALTER cannot use a non-constant DEFAULT, so the column
        # is added nullable and backfilled deterministically: an existing row
        # has never been edited, so updated_at = created_at. The backfill also
        # heals any NULL left by a crash between these two statements (a
        # re-run is a pure no-op on healthy rows). Fresh DBs get NOT NULL
        # DEFAULT datetime('now') from _SCHEMA; writers must bump this on
        # every content change.
        con.execute("ALTER TABLE messages ADD COLUMN updated_at TEXT")
    con.execute(
        "UPDATE messages SET updated_at = created_at WHERE updated_at IS NULL"
    )
    # Schema version history: 0/1 = pre-v1.1 (implicit), 2 = messages.updated_at.
    # Nothing branches on this yet - it exists so future migrations CAN.
    if con.execute("PRAGMA user_version").fetchone()[0] < 2:
        con.execute("PRAGMA user_version = 2")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_chat_active "
        "ON messages(chat_id, active)"
    )
    # Self-heal before the unique index: if external edits (or a foreign
    # build) ever left a group with several active rows, demote all but the
    # newest - otherwise the CREATE UNIQUE INDEX below aborts every boot
    # with no recovery path. Idempotent, no-op on healthy databases.
    con.execute(
        "UPDATE messages SET active = 0 "
        "WHERE variant_group IS NOT NULL AND active = 1 AND id NOT IN ("
        "  SELECT MAX(id) FROM messages "
        "  WHERE variant_group IS NOT NULL AND active = 1 "
        "  GROUP BY variant_group"
        ")"
    )
    # One active row per variant group, enforced by the engine. Writers must
    # deactivate BEFORE activating/inserting or this index fires mid-statement.
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_per_group "
        "ON messages(variant_group) WHERE variant_group IS NOT NULL AND active = 1"
    )
    # Hot-query coverage: the chat list orders by (updated_at DESC, id DESC)
    # and orphan detection counts rows by sha256 - both were table scans.
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_chats_updated_at "
        "ON chats(updated_at DESC, id DESC)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_attachments_sha256 "
        "ON attachments(sha256)"
    )
    _migrate_notebook(con)


#: What THIS build understands. The guard at the top of _migrate refuses a file
#: stamped higher; the bump at the bottom of _migrate_notebook records success.
#: History: 0/1 pre-v1.1 · 2 messages.updated_at · 3 notebook + boundaries.
_SCHEMA_VERSION = 3


def _migrate_notebook(con: sqlite3.Connection) -> None:
    """The notebook, the boundaries, and their bookkeeping.

    DELIBERATELY HERE AND NOT IN _SCHEMA, and the reason is measured rather
    than stylistic: init_db runs _SCHEMA through executescript(), which commits
    implicitly before every statement (see its docstring). Tables created there
    could not share a transaction with the user_version bump that records them,
    so a crash mid-way would leave a database that says it migrated and has not.
    Everything in _migrate runs inside the transaction init_db closes, so the
    tables and the stamp land together or not at all.

    Index names carry a version suffix on purpose. CREATE INDEX IF NOT EXISTS
    is a no-op when the NAME exists, whatever the definition says - so an index
    whose columns change later would silently keep its old shape on every
    machine that already ran the old one. A new name is the only way to make
    the change actually happen.
    """
    # con.execute per statement, NOT executescript. executescript commits
    # any pending transaction and leaves autocommit on - which would throw
    # away the only reason these tables are here instead of in _SCHEMA. The
    # docstring above promises the shape and the stamp land together; with
    # executescript that promise was simply false.
    con.execute("""
CREATE TABLE IF NOT EXISTS notebook_entries (
          id                INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id           INTEGER NOT NULL REFERENCES chats(id),
          position          INTEGER NOT NULL,
          kind              TEXT    NOT NULL DEFAULT 'fact'
                            CHECK (kind IN ('fact','event','relationship',
                                            'open_thread','entity','state',
                                            'knowledge','preference')),
          text              TEXT    NOT NULL,
          evidence          TEXT,
          durability        TEXT    NOT NULL DEFAULT 'permanent'
                            CHECK (durability IN ('scene','session','permanent')),
          importance        INTEGER NOT NULL DEFAULT 2
                            CHECK (importance BETWEEN 1 AND 3),
          pinned            INTEGER NOT NULL DEFAULT 0,
          retired_at        TEXT,
          superseded_by     INTEGER REFERENCES notebook_entries(id),
          excluded_reason   TEXT,
          status            TEXT    NOT NULL DEFAULT 'accepted'
                            CHECK (status IN ('proposed','accepted')),
          provenance        TEXT    NOT NULL DEFAULT 'user'
                            CHECK (provenance IN ('user','model')),
          source_message_id INTEGER REFERENCES messages(id),
          created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
          updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    con.execute("""
CREATE TABLE IF NOT EXISTS boundaries (
          id                INTEGER PRIMARY KEY AUTOINCREMENT,
          scope             TEXT    NOT NULL CHECK (scope IN ('global','chat')),
          chat_id           INTEGER REFERENCES chats(id),
          label             TEXT    NOT NULL,
          phrasing          TEXT    NOT NULL,
          severity          TEXT    NOT NULL
                            CHECK (severity IN ('hard','veiled','soft')),
          polarity          TEXT    NOT NULL DEFAULT 'avoid'
                            CHECK (polarity IN ('avoid','seek')),
          on_violation      TEXT    NOT NULL DEFAULT 'pause'
                            CHECK (on_violation IN ('rewind','fast_forward',
                                                    'pause','hard_stop')),
          source            TEXT    NOT NULL DEFAULT 'explicit'
                            CHECK (source IN ('explicit','inferred')),
          rating_ceiling    TEXT    CHECK (rating_ceiling IN ('G','PG','PG-13','R')),
          exempt_from_trim  INTEGER NOT NULL DEFAULT 1,
          last_confirmed_at TEXT,
          active            INTEGER NOT NULL DEFAULT 1,
          created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
          updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),
          -- A boundary this app INFERRED may never be a hard limit. Enforced by
          -- the engine and not by a code path, because a code path can be
          -- bypassed by the next writer and this one cannot.
          CHECK (NOT (source = 'inferred' AND severity = 'hard')),
          -- Scope and owner say the same thing or the row is nonsense.
          CHECK ((scope = 'global') = (chat_id IS NULL))
        )
    """)
    con.execute("""
CREATE TABLE IF NOT EXISTS notebook_extractions (
          -- NOT NULL is not implied: SQLite's legacy quirk lets a non-INTEGER
          -- PRIMARY KEY hold unlimited NULLs, so an idempotency key computed
          -- as None would silently stop deduplicating anything.
          work_key        TEXT    PRIMARY KEY NOT NULL,
          chat_id         INTEGER NOT NULL REFERENCES chats(id),
          from_message_id INTEGER NOT NULL,
          to_message_id   INTEGER NOT NULL,
          -- 'running' exists so a crash leaves a trace. Without it a row only
          -- appears after the call returns, the same range recomputes the same
          -- key on the next attempt, and the model is paid twice for work the
          -- first attempt may already have finished.
          status          TEXT    NOT NULL
                          CHECK (status IN ('running','done','failed','skipped')),
          started_at      TEXT,
          request_id      TEXT,
          finish_reason   TEXT,
          skip_reason     TEXT,
          error_type      TEXT,
          tokens_in       INTEGER,
          tokens_out      INTEGER,
          cost            REAL,
          created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # The NOT NULL defence three lines above was applied to one table and not
    # its neighbour. A `day` computed as None would accumulate unlimited NULL
    # rows, the daily total would never aggregate them, and a spend cap built
    # on this table would pass forever by summing rows it can never find - a
    # cap that reads as enforced and is not.
    #
    # Rebuilt rather than patched because ALTER cannot add NOT NULL to an
    # existing column. Safe only while the table has no rows, so that is
    # checked rather than assumed: a populated table is left alone and the
    # weaker constraint kept, because losing somebody's spend history to a
    # tightening migration would be the worse bug.
    spend_cols = {r[1]: r for r in
                  con.execute("PRAGMA table_info(notebook_spend)").fetchall()}
    # `.get`, not `[...]`. A `notebook_spend` from a divergent build without
    # a `day` column raised KeyError inside _migrate, which propagates out of
    # init_db and refuses to open the vault - with a traceback instead of the
    # deliberate sentence the downgrade guard produces.
    if spend_cols.get("day") and not spend_cols["day"][3]:      # 3 == notnull
        rows = con.execute("SELECT COUNT(*) FROM notebook_spend").fetchone()[0]
        if rows == 0:
            con.execute("DROP TABLE notebook_spend")
        else:
            logger.warning(
                "notebook_spend keeps its nullable day column: %d rows present.",
                rows)

    con.execute("""
CREATE TABLE IF NOT EXISTS notebook_spend (
          day        TEXT    PRIMARY KEY NOT NULL,
          calls      INTEGER NOT NULL DEFAULT 0,
          tokens_in  INTEGER NOT NULL DEFAULT 0,
          tokens_out INTEGER NOT NULL DEFAULT 0,
          cost       REAL    NOT NULL DEFAULT 0
        )
    """)

    chat_cols = {r[1] for r in con.execute("PRAGMA table_info(chats)").fetchall()}
    if "use_global_boundaries" not in chat_cols:
        # NOT NULL DEFAULT 1 is legal in ALTER because 1 is a constant, unlike
        # the datetime() default that forced messages.updated_at to be nullable.
        con.execute(
            "ALTER TABLE chats ADD COLUMN "
            "use_global_boundaries INTEGER NOT NULL DEFAULT 1"
        )
    if "notebook_auto_accept_override" not in chat_cols:
        # NULL = follow the app-wide setting. A chat opened from an imported
        # card gets 0, so a stranger's text can never be auto-accepted even
        # while the global switch is on.
        con.execute(
            "ALTER TABLE chats ADD COLUMN notebook_auto_accept_override INTEGER"
        )

    # Orphan sweep BEFORE the indexes. An older build restored from
    # app.db.premigrate.bak can carry rows whose chat is gone, and foreign keys
    # are not enforced on this connection (init_db does not set the pragma), so
    # nothing stopped them arriving. integrity_check would not find these
    # either - only foreign_key_check does, and it reports rather than repairs.
    con.execute(
        "DELETE FROM notebook_entries "
        "WHERE chat_id NOT IN (SELECT id FROM chats)"
    )
    con.execute(
        "DELETE FROM notebook_extractions "
        "WHERE chat_id NOT IN (SELECT id FROM chats)"
    )
    con.execute(
        "DELETE FROM boundaries "
        "WHERE scope = 'chat' AND chat_id NOT IN (SELECT id FROM chats)"
    )

    # Self-heal before the unique index, same reasoning as the variant-group
    # one above: duplicate positions would abort the CREATE on every boot with
    # no way back in. Renumber the losers to the end rather than deleting them -
    # the owner's rule is that a note never disappears.
    #
    # A FLAT OFFSET IS NOT ENOUGH, and the first version of this used one. It
    # added the same constant to every loser, which turns N rows sharing a
    # position into N-1 rows sharing a NEW position - the index still aborts,
    # every boot, with the vault refusing to open and no way back in. It also
    # lands on whatever already sits at the shifted number. Two independent
    # reviews reproduced it before it shipped.
    #
    # Each loser therefore gets its own slot past its chat's current maximum,
    # taken one at a time so the maximum moves with them, ordered by id so the
    # outcome is the same on every machine.
    for entry_id, chat in con.execute("""
        SELECT id, chat_id FROM notebook_entries
        WHERE id NOT IN (
          SELECT MIN(id) FROM notebook_entries GROUP BY chat_id, position
        ) ORDER BY chat_id, id
    """).fetchall():
        top = con.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM notebook_entries "
            "WHERE chat_id = ?", (chat,)).fetchone()[0]
        con.execute("UPDATE notebook_entries SET position = ? WHERE id = ?",
                    (top, entry_id))
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_notebook_order_v1 "
        "ON notebook_entries(chat_id, position)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_notebook_live_v1 "
        "ON notebook_entries(chat_id, status, retired_at)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_boundaries_scope_v1 "
        "ON boundaries(scope, chat_id, active)"
    )
    # The hottest query in the whole feature had no index at all. The worker
    # resolves its resume point on EVERY offered turn -
    #   SELECT MAX(to_message_id) WHERE chat_id = ? AND status = 'done'
    # - against a table that gains a row per attempt, failures and skips
    # included. Three more scans hang off the same gap: the chat-delete sweep,
    # the message-delete rollback, and the status counters.
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_extractions_chat_v1 "
        "ON notebook_extractions(chat_id, status, to_message_id)"
    )
    # And the child-side index the FK needs. Without it SQLite rescans
    # notebook_entries ONCE PER DELETED ROW while validating
    # `DELETE FROM messages`, which is quadratic: measured at 0.03s for 500
    # notes and 0.23s for 2000 across 4000 messages, under a held write lock.
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_notebook_source_v1 "
        "ON notebook_entries(source_message_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_notebook_superseded_v1 "
        "ON notebook_entries(superseded_by)"
    )

    if con.execute("PRAGMA user_version").fetchone()[0] < _SCHEMA_VERSION:
        con.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def init_db() -> None:
    """Create all tables if they don't exist. Requires an UNLOCKED vault -
    the schema (incl. journal_mode=WAL) is written into the encrypted file,
    so runs at unlock time, not process startup.

    executescript() commits implicitly before running each statement, which is
    intentional here - DDL does not need transactional rollback semantics.
    """
    key = get_key()  # raises VaultLockedError while locked
    con = sqlite3.connect(DB_PATH)
    try:
        _key_pragma(con, key)
        con.executescript(_SCHEMA)
        _migrate(con)
        con.commit()
        logger.info("Database ready at %s", DB_PATH)
    finally:
        con.close()


@contextlib.contextmanager
def get_db(busy_timeout_ms: int = 15000) -> Generator[sqlite3.Connection, None, None]:
    """Yield a keyed connection. Commits on success, rolls back on exception.
    Raises VaultLockedError while the vault is locked (the API layer maps it
    to HTTP 423 before any router code normally runs).

    foreign_keys is enabled per-connection (SQLite default is OFF) so the
    REFERENCES constraints in the schema are actually enforced.

    busy_timeout_ms (v1.1 audit L6): how long to queue on a held write lock
    before raising SQLITE_BUSY. Defaults to 15s for the normal off-loop paths;
    a caller running SYNCHRONOUSLY on the event loop (the stream abort cleanup)
    passes a short value so a contended lock can never freeze the loop.
    """
    key = get_key()  # raises VaultLockedError while locked
    con = sqlite3.connect(DB_PATH)
    # The connect and the four pragmas below sat OUTSIDE the try, so anything
    # they raised - a wrong key, a corrupt header, a bad busy_timeout - left
    # the generator with an open handle that nothing ever closed. On Windows
    # that handle then blocks the rename in adopt_orphaned_enc_tmp and in the
    # migration swap, which is how a leak in the read path turns into a
    # failure in the recovery path.
    try:
        con.row_factory = sqlite3.Row
        _key_pragma(con, key)
        con.execute("PRAGMA foreign_keys = ON")
        # WAL + NORMAL is the standard pairing: fsync on checkpoint instead of
        # on every commit (FULL). Commits stop stalling the caller on slow
        # disks and WAL still guarantees corruption-free crashes (at most the
        # last commit is lost - acceptable for a local chat log).
        con.execute("PRAGMA synchronous = NORMAL")
        # Queue on a held write lock instead of raising SQLITE_BUSY
        # immediately. A cascade delete of an image-heavy chat can hold the
        # writer for a moment; without this, a concurrent BEGIN IMMEDIATE 500s
        # rather than waiting. (v1.1 FB9.) int() keeps the value trusted
        # before it enters the PRAGMA.
        con.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        yield con
        con.commit()
    except Exception:
        # rollback() on a connection whose setup failed is a no-op, so the
        # setup pragmas can share this handler; what matters is that they now
        # share the finally.
        con.rollback()
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Vault plumbing: key checks, rekey, plaintext migration
# ---------------------------------------------------------------------------

def check_key(key: bytes, db_path: str | None = None) -> bool:
    """True if this key opens the DB file (the DB is the final authority on
    key correctness - used by DB-validated recovery).

    A zero-length file is NEVER a positive answer. sqlite treats an empty file
    as a brand-new database, so `SELECT count(*) FROM sqlite_master` succeeds
    under every possible key - and a 0-byte app.db is reachable in one step
    (first run: /vault/init writes salt.bin + verifier.bin, init_db() creates
    the file, and the process dies before the schema lands). DB-validated
    recovery then ACCEPTED an arbitrary typo'd passphrase and overwrote
    verifier.bin with the HMAC of that wrong key, after which the user's real
    passphrase returned wrong_passphrase forever with no reset path in the UI.
    """
    path = Path(db_path or DB_PATH)
    try:
        if path.stat().st_size == 0:
            return False
    except OSError:
        return False
    con = sqlite3.connect(path)
    try:
        _key_pragma(con, key)
        con.execute("SELECT count(*) FROM sqlite_master")
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        con.close()


def rekey_file(path: str, new_key: bytes, current_key: bytes) -> None:
    """Re-encrypt an arbitrary encrypted DB FILE under a new raw key.

    Used for the sidecar snapshots (app.db.premigrate.bak, stale
    app.db.rekey.bak-*): they are complete copies of the vault, so a passphrase
    rotation that left them under the old key did not actually revoke anything.
    Raises on a bad key or an unreadable file - the caller decides how loud
    that is.
    """
    con = sqlite3.connect(path)
    try:
        _key_pragma(con, current_key)
        con.execute("SELECT count(*) FROM sqlite_master")  # fail fast on bad key
        _key_pragma(con, new_key, rekey=True)
    finally:
        con.close()


def rekey_db(new_key: bytes, current_key: bytes | None = None) -> None:
    """Re-encrypt the whole DB under a new raw key (passphrase change).
    Caller must take a backup first. NOTE: PRAGMA rekey can silently no-op
    under a concurrent write lock, so the caller MUST verify with
    check_key(new_key) before trusting it (see crypto.KeyVault.change_passphrase).

    current_key (v1.1 FB5b): the CURRENT key, passed explicitly. Reading it
    from vault_state here would force the LOCKED change-passphrase path to
    set_key(old_key) first, unlocking the whole API (the 423 gate would open)
    for the entire rekey window. The keyed caller passes it instead."""
    key = current_key if current_key is not None else get_key()
    con = sqlite3.connect(DB_PATH)
    try:
        _key_pragma(con, key)
        con.execute("SELECT count(*) FROM sqlite_master")  # fail fast on bad key
        _key_pragma(con, new_key, rekey=True)
    finally:
        con.close()


def backup_encrypted(dest_path: str, key: bytes | None = None) -> None:
    """Write a complete, WAL-consistent encrypted copy of the live DB using
    SQLite's online-backup API. Preferred over a raw file copy: it captures
    un-checkpointed WAL frames and never tears pages, so it is a trustworthy
    pre-rekey safety net.

    key (v1.1 FB5b): explicit current key for the locked change-passphrase
    path; falls back to vault_state when omitted (unlocked callers)."""
    the_key = key if key is not None else get_key()
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest_path)
    try:
        _key_pragma(src, the_key)
        _key_pragma(dst, the_key)
        src.backup(dst)
    finally:
        dst.close()
        src.close()


#: SQLite's companions to the main database file. Named once, because two
#: places have to agree about them and a list that drifts is a leak.
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def discard_plaintext_sidecars(src: Path) -> list[str]:
    """Destroy the journal files beside a database that is not there.

    PLAINTEXT PAGES, not bookkeeping. `wal_checkpoint(TRUNCATE)` folds the WAL
    into the main file, but it does not raise when it cannot finish: a reader
    still holding the file - an antivirus scan, a leftover connection from a
    crash - makes it return without truncating, and committed chat pages stay
    in `-wal`. Leaving them was leaving the conversation recoverable
    immediately after the migration whose whole purpose was to seal it.

    CALLED ONLY WHERE `src` DOES NOT EXIST, and that is the entire design.
    Both callers sit between two renames, at the instant the live database has
    been moved away and its replacement has not yet arrived. Anything wearing
    the name `app.db-wal` at that moment is provably an orphan, because there
    is no `app.db` for it to belong to.

    The alternative that was rejected: report these through
    `plaintext_backups()` so the existing discard route removes them. That
    route needs no passphrase, by design, because it deletes files that are
    readable without one. Handing it these names would give a key-free route
    the authority to shred a LIVE encrypted WAL holding committed pages that
    have not reached the main file. The fix would have been a worse defect
    than the one it closed.

    Returns the names it could not destroy, so a caller can say so. `discard`
    reports failure by returning False and, for an ordinary OSError, says
    nothing at all - so a silent survivor was possible even with no crash.
    """
    stuck: list[str] = []
    for suffix in _SIDECAR_SUFFIXES:
        sidecar = src.with_name(src.name + suffix)
        if not os.path.lexists(sidecar):
            continue
        try:
            if not secure_delete.discard(sidecar):
                stuck.append(sidecar.name)
        except BaseException:  # noqa: BLE001 - see below
            # NOTHING may escape from here, and the reason is where this runs.
            # Both callers are between two renames, at the one instant DB_PATH
            # does not exist. An exception escaping leaves the live database
            # path EMPTY - a worse state than the leak this function closes.
            #
            # `discard` documents itself as never raising, and for OSError it
            # does not. MemoryError is the one that gets through: shred does
            # os.urandom(st_size), allocating the whole file at once, and a
            # -wal gets large in exactly the case this exists for - a
            # checkpoint that could not finish.
            logger.exception("Could not destroy %s", sidecar.name)
            stuck.append(sidecar.name)
    return stuck


def adopt_orphaned_enc_tmp(key: bytes) -> bool:
    """Crash recovery: if a migration crashed between its two swap renames,
    the live app.db is gone but a valid encrypted copy sits at app.db.enc-tmp.
    Adopt it (rename into place) so the next init_db doesn't create an empty
    vault over real data. Returns True if an adoption happened.

    "Gone" used to mean `not src.exists()`, which reads a 0-byte app.db as a
    real database and refuses to adopt. check_key's own docstring proves that
    stub is reachable in one step - /vault/init writes the identity files,
    init_db() creates the file, the process dies before the schema lands - so
    a crash in that window left the live path holding an empty stub, adoption
    declined, and init_db() then built a fresh empty vault while every message
    the user had sat untouched in app.db.enc-tmp. No error anywhere: the app
    simply opened empty.

    Adoption is deliberately limited to the two unambiguous cases, absent and
    zero-length. A non-empty file that will not open under this key is NOT
    assumed to be junk - it could be a DB keyed differently from the verifier
    that authorised this unlock - so it is left exactly where it is and
    reported instead.
    """
    src = Path(DB_PATH)
    enc_tmp = src.with_name(src.name + ".enc-tmp")
    if not enc_tmp.exists():
        return False

    try:
        live_size = src.stat().st_size
        live_present = True
    except OSError:
        live_size = 0
        live_present = False

    if live_present and live_size > 0:
        # P4: a second database file sitting beside the live one is never
        # allowed to be silent, whether or not we act on it.
        #
        # The wording is careful about what this branch actually checked, which
        # is only that both files are non-empty. It used to assert "It is a full
        # copy of the vault" without calling check_key at all - and the live
        # file is not necessarily a vault either: a still-plaintext app.db
        # awaiting migration lands here too, where a stranded .enc-tmp is far
        # more likely to be a previous migration's own scratch file than a
        # second vault. Stating more than was measured is how a log line sends
        # somebody to the wrong conclusion at the worst moment.
        logger.error(
            "An encrypted DB is present at %s beside a live database and was "
            "NOT adopted. Neither file was opened, so which one holds what is "
            "unknown here; it will not be removed automatically. Inspect both "
            "before deleting either.",
            enc_tmp.name,
        )
        return False

    if not check_key(key, str(enc_tmp)):
        logger.error(
            "An orphaned encrypted DB is present at %s but does not open with "
            "the current key, so it was NOT adopted and the live database was "
            "left alone. It will not be removed automatically.",
            enc_tmp.name,
        )
        return False

    if live_present:
        # Zero bytes, so nothing can be lost - but move it aside rather than
        # unlink, because "the recovery path deleted a file" is a sentence no
        # one should ever have to read while diagnosing one.
        stub_bak = src.with_name(src.name + ".empty-stub-bak")
        try:
            src.replace(stub_bak)
        except OSError:
            logger.exception(
                "Could not move the empty %s aside; adoption skipped this "
                "pass and will be retried on the next unlock.", src.name,
            )
            return False

    # HERE, and only here. This is the other instant at which DB_PATH is empty:
    # the migration crashed between its own two renames, so the plaintext
    # sidecars it never reached are still sitting at the live name, and the
    # encrypted file is about to take that name. One line later they would be
    # indistinguishable from a healthy vault's own journal files, and this
    # function is the last thing that ever looks.
    stuck = discard_plaintext_sidecars(src)

    enc_tmp.replace(src)
    if stuck:
        logger.warning(
            "Plaintext database sidecars survived the recovery and are still "
            "readable on disk: %s", ", ".join(stuck))
    logger.info(
        "Adopted orphaned encrypted DB from an interrupted migration%s.",
        " (an empty stub was moved aside)" if live_present else "",
    )
    return True


#: Every stranded encrypted copy, not just the one at the canonical name.
#: migrate_plaintext_to_encrypted moves a copy it finds in its way to
#: app.db.enc-tmp.orphan-bak-<ts> rather than deleting it, and a family that
#: only the migration knew about would be a copy of the vault that /vault/status
#: cannot see and /vault/discard-orphaned-copy cannot remove - which is the
#: shape of bug this whole group of fields exists to end.
ORPHAN_GLOB = ".enc-tmp*"


def orphaned_enc_tmp_paths() -> list[Path]:
    """Every stranded encrypted copy beside the vault, in a stable order.

    SIDECARS ARE NOT COPIES. `ORPHAN_GLOB` is deliberately wide, and a wide
    glob also catches `app.db.enc-tmp-wal`, which ATTACH can leave behind and
    which is a journal, not a database. It cannot open under any key, so it
    failed `check_key`, and both readers below insist that EVERY match opens -
    correctly, since one unreadable file among several is exactly the case
    that must not be offered a delete button.

    The two rules were each right and their product was not: a single stray
    journal file answered `different_key` forever, permanently disabling the
    only route that removes a stranded copy of the vault and putting it in the
    `unrevoked` list at every passphrase change. Sidecars are excluded here,
    once, rather than each reader being taught to forgive them - a reader that
    forgives an unopenable file is the safety property gone.
    """
    src = Path(DB_PATH)
    try:
        found = sorted(src.parent.glob(src.name + ORPHAN_GLOB))
    except OSError:
        return []
    return [p for p in found if not p.name.endswith(_SIDECAR_SUFFIXES)]


def orphaned_enc_tmp_present() -> bool:
    """True while a full encrypted copy of the vault is stranded on disk.

    Surfaced through /vault/status so an un-adopted orphan is visible in the
    UI rather than only in a log line nobody opens.
    """
    return bool(orphaned_enc_tmp_paths())


def orphaned_enc_tmp_opens_with(key: bytes) -> bool:
    """Whether EVERY stranded copy is THIS vault, under the key we hold.

    The distinction decides whether they may be deleted at all. Adoption
    already declined them, and there are only two ways that happens (see
    adopt_orphaned_enc_tmp): the live database is fine and this is a redundant
    second copy, or it does not open under this key - in which case it may be
    a vault keyed to a DIFFERENT passphrase, and deleting it would destroy the
    only copy of something this user cannot currently read.

    all(), deliberately: one unreadable copy among several is the case that
    must not be reported as "these are duplicates, here is a delete button".
    """
    paths = orphaned_enc_tmp_paths()
    if not paths:
        return False
    return all(check_key(key, str(path)) for path in paths)


def discard_orphaned_enc_tmp(key: bytes) -> tuple[bool, str]:
    """Shred the stranded encrypted copies. Returns (removed, reason if not).

    REFUSES a copy that does not open under the current key. That is the
    whole safety property: an encrypted file nobody here can read is not junk
    to be tidied away, it is data whose passphrase we do not have. Every other
    deletion in this app removes something the user can already read; this one
    would not, so it does not get to guess.

    All or nothing when there is more than one, and checked BEFORE anything is
    destroyed: a run that shredded the readable copies and then refused the
    rest would answer False about work it had already done.
    """
    paths = orphaned_enc_tmp_paths()
    if not paths:
        return False, "not_present"
    for path in paths:
        if not check_key(key, str(path)):
            logger.warning(
                "Refusing to delete %s: it does not open with the current key, "
                "so it may be a vault under a different passphrase.", path.name)
            return False, "different_key"
    for path in paths:
        if not secure_delete.shred(path):
            logger.warning(
                "%s could not be deleted and is still on disk.", path.name)
            return False, "in_use"
    logger.info("Discarded %d orphaned encrypted copy(ies).", len(paths))
    return True, ""


#: Both rotations - the passphrase change and the KDF upgrade - copy the whole
#: database here before touching it and remove it afterwards. A process killed
#: in that window leaves a complete copy of the vault behind.
ROTATION_BACKUP_GLOB = ".rekey.bak-*"


def rotation_backup_paths() -> list[Path]:
    """Every full copy a rotation left behind, in a stable order."""
    src = Path(DB_PATH)
    try:
        return sorted(src.parent.glob(src.name + ROTATION_BACKUP_GLOB))
    except OSError:
        return []


def plaintext_backups() -> list[Path]:
    """Every pre-vault copy of the database still lying about, unencrypted.

    Migration renames the old plaintext app.db to app.db.plain.bak-<ts> and
    keeps it - deliberately, because a migration that verified wrong would
    otherwise have destroyed the only copy. But nothing ever removed it, and
    nothing reported it either: the user saw one banner on the single launch
    that migrated, and after that the file was invisible.

    It is a complete SQLite database. Every message, every character card,
    every system prompt, in the clear, beside a vault the UI calls encrypted.
    Sorted so the caller shows them in a stable order.
    """
    src = Path(DB_PATH)
    try:
        return sorted(src.parent.glob(src.name + ".plain.bak-*"))
    except OSError:
        return []


def empty_stub_present() -> bool:
    """True while the stub adoption moved aside is still on disk.

    adopt_orphaned_enc_tmp renames a 0-byte live app.db to .empty-stub-bak
    rather than unlinking it, so that no one diagnosing a recovery ever has to
    read the sentence "the recovery path deleted a file". Sound - but nothing
    then reported the result. The name appears in no route, no response field
    and no screen, and there is no way to remove it from inside the app: an
    unexplained file next to the vault, permanently, whose only mention is one
    log line from the launch that produced it.
    """
    src = Path(DB_PATH)
    return src.with_name(src.name + ".empty-stub-bak").exists()


def discard_empty_stub() -> tuple[bool, str]:
    """Remove the moved-aside stub. Returns (removed, reason if not).

    REFUSES anything that is not zero bytes. Adoption can only create this name
    from a file it measured at zero, so a non-empty one means something else
    put it there, and this is the one deletion in the app whose safety rests
    entirely on "there is provably nothing in it". Re-measuring costs one stat
    and turns that from an assumption into a check.

    shred rather than unlink even at zero bytes: a name can be a junction or a
    hardlink whatever its length, and secure_delete is what knows to refuse
    those.
    """
    src = Path(DB_PATH)
    stub = src.with_name(src.name + ".empty-stub-bak")
    try:
        size = stub.stat().st_size
    except OSError:
        return False, "not_present"
    if size != 0:
        logger.error(
            "%s is %d bytes, not the empty stub adoption leaves behind. It was "
            "NOT removed; something else wrote to that name.", stub.name, size,
        )
        return False, "not_empty"
    if not secure_delete.shred(stub):
        return False, "not_removed"
    logger.info("Discarded the empty stub left by an earlier recovery.")
    return True, ""


def discard_plaintext_backups() -> tuple[int, list[str], list[str]]:
    """Shred the pre-vault copies.

    Returns (removed, could-not, would-not), and the last two are separate
    because they need opposite sentences from the user.

    Overwritten before unlinking, because the point is that the content stops
    existing, not that the directory entry does.

    The names of failures are returned rather than swallowed: a route that
    answered a flat "done" while a full plaintext database stayed readable
    would be making exactly the promise this whole feature exists to keep.

    K-49, the third list. `shred` refuses a file whose bytes answer to more
    than one name, and it is right to - overwriting would destroy whatever the
    other name belongs to, which is how a shred becomes a weapon. But that
    refusal came back as an ordinary failure, and the user was told something
    else had the file open. So they closed programs, retried, and eventually
    deleted the file by hand - which unlinks ONE name and leaves the whole
    plaintext database readable under the other. The one fact that changes
    what they do was the one fact missing.

    Deliberately NOT solved by unlinking our name without overwriting. That
    would clear the folder and the notice while the content stayed exactly as
    readable as before, and this app does not get to swap a true alarm for a
    tidy screen. Naming the file, and naming what is actually true about it,
    is the whole remedy available from in here.
    """
    removed = 0
    left: list[str] = []
    shared: list[str] = []
    for backup in plaintext_backups():
        if secure_delete.shred(backup):
            removed += 1
        elif secure_delete.is_shared(backup):
            shared.append(backup.name)
        else:
            left.append(backup.name)
    if left:
        logger.warning(
            "%d plaintext backup(s) could not be deleted and are still "
            "readable on disk: %s", len(left), ", ".join(left))
    if shared:
        logger.warning(
            "%d plaintext backup(s) were left alone because their contents "
            "answer to another name on this disk, which overwriting them "
            "would have destroyed: %s", len(shared), ", ".join(shared))
    if removed and not left and not shared:
        logger.info("Discarded %d plaintext pre-vault backup(s).", removed)
    return removed, left, shared


def _rename_with_retry(src: Path, dest: Path, attempts: int = 5) -> None:
    """os.replace can transiently fail on Windows (WinError 32) when an AV
    scanner or a status probe momentarily holds the file. Retry briefly
    before giving up."""
    last: OSError | None = None
    for i in range(attempts):
        try:
            src.replace(dest)
            return
        except PermissionError as exc:  # WinError 32 surfaces as PermissionError
            last = exc
            time.sleep(0.1 * (i + 1))
    if last is not None:
        raise last


def is_plaintext_db(db_path: str | None = None) -> bool:
    """True if the file at db_path is a readable UNENCRYPTED SQLite database
    (the pre-vault app.db). An unkeyed SQLCipher connection reads plaintext
    files fine and fails on encrypted ones - that asymmetry is the probe."""
    path = Path(db_path or DB_PATH)
    try:
        if path.stat().st_size == 0:
            # Same asymmetry problem as check_key: an empty file reads fine
            # unkeyed, so it would be reported as a plaintext pre-vault DB and
            # sent through migrate_plaintext_to_encrypted. There is nothing in
            # it to migrate.
            return False
    except OSError:
        return False
    con = sqlite3.connect(path)
    try:
        con.execute("SELECT count(*) FROM sqlite_master")
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        con.close()


#: The four things that can be sitting at DB_PATH. Strings rather than an enum
#: because these travel straight into log lines and test assertions, and a
#: repr like <DbKind.EMPTY: 2> in a failure message helps nobody.
DB_ABSENT = "absent"
DB_EMPTY = "empty"
DB_PLAINTEXT = "plaintext"
DB_ENCRYPTED = "encrypted"


def classify_db_file(db_path: str | None = None) -> str:
    """What is actually at db_path: absent, empty, plaintext, or encrypted.

    Callers used to compose this answer themselves, as `path.exists() and not
    is_plaintext_db(path)`. That is two questions about a file that can change
    between them, and worse, it folds three unrelated answers into one False:
    is_plaintext_db() returns False for an encrypted database, for a file that
    vanished, AND for a 0-byte one.

    The 0-byte case is not hypothetical. check_key()'s docstring above spells
    out how it is reached in one step, and both callers of the old expression
    then treated it as "an encrypted database is present": /vault/status
    answered initialized=true and offered the unlock screen, while /vault/init
    answered 409 encrypted_db_without_identity. So an empty file produced an
    app that insisted the data was merely locked, refused to set itself up, and
    could not unlock either - DB-validated recovery refuses 0-byte files by
    design, so every passphrase came back "wrong". A locked door to an empty
    room, with the setup path walled off behind it.

    The handle is deliberately held open across the probe. On Windows a file
    opened this way cannot be renamed or deleted until it closes, so the file
    the probe reads is provably the one this size came from, which is the
    present-vs-readable race closed rather than narrowed. It also stops the
    probe from CREATING the file it was asked about: sqlite3.connect() on a
    missing path makes a fresh empty database and then cheerfully reports it as
    readable plaintext, which is how a lost app.db could have come back as a
    migration candidate.
    """
    path = Path(db_path or DB_PATH)
    try:
        with open(path, "rb") as handle:
            if os.fstat(handle.fileno()).st_size == 0:
                return DB_EMPTY
            return DB_PLAINTEXT if is_plaintext_db(str(path)) else DB_ENCRYPTED
    except OSError:
        # Missing, a directory, or unreadable. All three mean the same thing to
        # every caller: there is no database here to reason about.
        return DB_ABSENT


def migrate_plaintext_to_encrypted(key: bytes) -> str:
    """One-shot migration of a plaintext app.db into the vault.

    Order is crash-safe: the encrypted copy is built in a SIDE file first
    (sqlcipher_export from the still-untouched plaintext), verified, and only
    then swapped in; the plaintext original is kept as a .bak file (the user
    deletes it when satisfied). Returns the backup path.
    """
    src = Path(DB_PATH)
    ts = int(time.time())
    backup = src.with_name(src.name + f".plain.bak-{ts}")

    # The scratch name is normally app.db.enc-tmp, because that is the name
    # adopt_orphaned_enc_tmp watches for and this function's crash window is
    # what adoption exists to recover.
    #
    # If something is ALREADY there, it is not ours to remove. This used to be
    # `if enc_tmp.exists(): enc_tmp.unlink()`, three lines after the same
    # request flow ran adoption - which looks at that identical file and, when
    # it declines, logs "It is a full copy of the vault; it will not be removed
    # automatically. Inspect both before deleting either." The promise and its
    # breach were in the same unlock. Worse, discard_orphaned_enc_tmp REFUSES to
    # delete a copy that does not open under the current key, on the grounds
    # that an encrypted file nobody here can read is not junk but data whose
    # passphrase we do not have - and this unlink did not even ask.
    #
    # Deleting nothing and stepping aside is what fixes it. The stranded file
    # stays exactly where it was, /vault/status keeps reporting it as
    # orphaned_copy, and /vault/discard-orphaned-copy is still the only thing
    # that may remove it - a decision the user makes, with the key in hand.
    # Refusing to migrate at all was the other candidate and is a trap: that
    # route needs an unlocked vault, so a refusal here would leave the one file
    # blocking the unlock removable only by an unlock.
    # The scratch name is normally app.db.enc-tmp, because that is the name
    # adopt_orphaned_enc_tmp watches for and this function's crash window is
    # what adoption exists to recover.
    #
    # If something is ALREADY there, it is not ours to remove. This used to be
    # `if enc_tmp.exists(): enc_tmp.unlink()`, three lines after the same
    # request flow ran adoption - which looks at that identical file and, when
    # it declines, logs "It is a full copy of the vault; it will not be removed
    # automatically. Inspect both before deleting either." The promise and its
    # breach were in the same unlock. Worse, discard_orphaned_enc_tmp REFUSES to
    # delete a copy that does not open under the current key, on the grounds
    # that an encrypted file nobody here can read is not junk but data whose
    # passphrase we do not have - and this unlink did not even ask.
    #
    # Deleting nothing and moving it ASIDE is what fixes it. Renaming keeps
    # every byte, and the new name is chosen to stay inside two nets it must
    # not fall out of: ORPHAN_GLOB, so /vault/status keeps reporting it and
    # /vault/discard-orphaned-copy can still remove it on the user's word, and
    # the ".bak" family that _rekey_sidecars scans, so a passphrase rotation
    # re-keys it instead of leaving a complete vault readable under the
    # passphrase being rotated away from.
    #
    # Refusing to migrate at all was the first candidate and is a trap: the
    # refusal propagates out of _bootstrap_unlocked, /vault/unlock turns it
    # into a 500 and re-locks, and the route that could remove the blocking
    # file needs an unlocked vault.
    #
    # Migrating to a DIFFERENT scratch name was the second, and it is worse
    # than it looks: adopt_orphaned_enc_tmp watches this exact name, so a crash
    # in the two-rename window below would leave the stranger sitting where
    # recovery looks and the freshly exported vault under a name nothing knows.
    # The stranger has to move; the scratch name has to stay canonical.
    enc_tmp = src.with_name(src.name + ".enc-tmp")
    if enc_tmp.exists():
        aside = src.with_name(src.name + f".enc-tmp.orphan.bak-{ts}")
        logger.error(
            "An encrypted copy was already stranded at %s. It was NOT deleted; "
            "it is moved aside to %s and stays visible in the vault status.",
            enc_tmp.name, aside.name,
        )
        _rename_with_retry(enc_tmp, aside)

    con = sqlite3.connect(str(src))  # unkeyed: reads the plaintext source
    try:
        # Never passes through _key_pragma, so the rule is repeated rather
        # than inherited. This connection runs the whole database through
        # sqlcipher_export() - the single largest statement the app issues
        # and the likeliest one to spill.
        con.execute("PRAGMA temp_store = MEMORY")
        # Fold any un-checkpointed WAL frames into the main file FIRST, so the
        # plaintext backup (which is the main file only) is complete and the
        # export sees the same state.
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass  # non-WAL plaintext DBs have nothing to checkpoint
        # ATTACH + sqlcipher_export is SQLCipher's official encrypt-copy path.
        con.execute(
            f"ATTACH DATABASE ? AS encrypted KEY \"x'{key.hex()}'\"",
            (str(enc_tmp),),
        )
        con.execute("SELECT sqlcipher_export('encrypted')")
        con.execute("DETACH DATABASE encrypted")
    finally:
        con.close()

    if not check_key(key, str(enc_tmp)):
        # shred, not unlink: this is a failed but possibly PARTIAL encrypted
        # export of the user's whole database, and the file this app deletes is
        # the file whose bytes have to stop existing. secure_delete is the one
        # deletion primitive for exactly that reason - it also refuses a
        # redirected or hardlinked name, which a bare unlink does not.
        # shred, not unlink: this is a failed but possibly PARTIAL encrypted
        # export of the user's whole database, and the file this app deletes is
        # the file whose bytes have to stop existing. secure_delete is the one
        # deletion primitive for exactly that reason - it also refuses a
        # redirected or hardlinked name, which a bare unlink does not.
        secure_delete.discard(enc_tmp)
        raise RuntimeError("migration_verify_failed")

    # Swap: plaintext → backup, encrypted copy → live path (retry the renames
    # against transient Windows file locks). Order matters for crash recovery:
    # if we crash between the two, adopt_orphaned_enc_tmp() restores enc-tmp on
    # the next unlock.
    _rename_with_retry(src, backup)
    stuck = discard_plaintext_sidecars(src)
    _rename_with_retry(enc_tmp, src)
    if stuck:
        # Not fatal: the vault is built and the conversation is in it. But a
        # plaintext remnant that nothing can see is worse than one that
        # somebody can, so it goes in the log at least. The route that lets a
        # person act on it is a separate piece of work.
        logger.warning(
            "Plaintext database sidecars survived the migration and are still "
            "readable on disk: %s", ", ".join(stuck))
    logger.info("Plaintext DB migrated into vault; backup at %s", backup.name)
    return str(backup)


# ---------------------------------------------------------------------------
# SQL bound-parameter chunking (v1.1 FB12)
# ---------------------------------------------------------------------------

# SQLite's compiled bound-parameter ceiling is 32766 (SQLITE_MAX_VARIABLE_NUMBER).
# IN (...) lists built from a whole chat's message/attachment ids are chunked far
# below it so an image-heavy or thousand-message chat cannot 500 a read or delete
# with "too many SQL variables". 900 keeps statements small while a 1000-row test
# fixture still crosses a chunk boundary.
SQL_VAR_CHUNK = 900


def iter_chunks(seq: list, size: int | None = None):
    """Yield successive slices of `seq` no larger than `size` (SQL_VAR_CHUNK)."""
    step = size if size is not None else SQL_VAR_CHUNK
    for i in range(0, len(seq), step):
        yield seq[i:i + step]


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def get_setting_con(con, key: str, default: str | None = None) -> str | None:
    """Read one settings row on a connection the CALLER already owns.

    `get_setting` opens its own. Called from inside an open transaction that
    is holding the write lock, that second connection waits for a lock its own
    caller is holding - the whole busy_timeout, every time, for a value the
    transaction could have read for free.
    """
    row = con.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_setting(key: str, default: str | None = None) -> str | None:
    """Read one settings row. Returns default if the key does not exist."""
    with get_db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Upsert one settings row. Atomic via ON CONFLICT."""
    with get_db() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def delete_setting(key: str) -> None:
    """Delete one settings row. Silent if key does not exist."""
    with get_db() as con:
        con.execute("DELETE FROM settings WHERE key = ?", (key,))

