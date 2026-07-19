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
import time
from pathlib import Path
from typing import Generator

from sqlcipher3 import dbapi2 as sqlite3

from config import DB_PATH
from vault_state import VaultLockedError, get_key  # noqa: F401 - re-exported

logger = logging.getLogger(__name__)


def _key_pragma(con: "sqlite3.Connection", key: bytes, *, rekey: bool = False) -> None:
    """Apply the raw-key PRAGMA. Hex comes from bytes.hex() (charset [0-9a-f])
    so string interpolation is injection-safe; PRAGMA takes no parameters."""
    stmt = "rekey" if rekey else "key"
    con.execute(f"PRAGMA {stmt} = \"x'{key.hex()}'\"")

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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    cols = {r[1] for r in con.execute("PRAGMA table_info(messages)").fetchall()}
    if "variant_group" not in cols:
        # NULL = never regenerated; else the id of the group's FIRST row (the
        # anchor), so COALESCE(variant_group, id) is any row's group key.
        con.execute("ALTER TABLE messages ADD COLUMN variant_group INTEGER")
    if "active" not in cols:
        con.execute(
            "ALTER TABLE messages ADD COLUMN active INTEGER NOT NULL DEFAULT 1"
        )
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
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield a keyed connection. Commits on success, rolls back on exception.
    Raises VaultLockedError while the vault is locked (the API layer maps it
    to HTTP 423 before any router code normally runs).

    foreign_keys is enabled per-connection (SQLite default is OFF) so the
    REFERENCES constraints in the schema are actually enforced.
    """
    key = get_key()  # raises VaultLockedError while locked
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    _key_pragma(con, key)
    con.execute("PRAGMA foreign_keys = ON")
    # WAL + NORMAL is the standard pairing: fsync on checkpoint instead of on
    # every commit (FULL). Commits stop stalling the caller on slow disks and
    # WAL still guarantees corruption-free crashes (at most the last commit
    # is lost - acceptable for a local chat log). Per-connection pragma.
    con.execute("PRAGMA synchronous = NORMAL")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Vault plumbing: key checks, rekey, plaintext migration
# ---------------------------------------------------------------------------

def check_key(key: bytes, db_path: str | None = None) -> bool:
    """True if this key opens the DB file (the DB is the final authority on
    key correctness - used by DB-validated recovery)."""
    path = db_path or DB_PATH
    if not Path(path).exists():
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


def rekey_db(new_key: bytes) -> None:
    """Re-encrypt the whole DB under a new raw key (passphrase change).
    Caller must hold the CURRENT key in vault_state and take a backup first.
    NOTE: PRAGMA rekey can silently no-op under a concurrent write lock, so
    the caller MUST verify with check_key(new_key) before trusting it (see
    crypto.KeyVault.change_passphrase)."""
    key = get_key()
    con = sqlite3.connect(DB_PATH)
    try:
        _key_pragma(con, key)
        con.execute("SELECT count(*) FROM sqlite_master")  # fail fast on bad key
        _key_pragma(con, new_key, rekey=True)
    finally:
        con.close()


def backup_encrypted(dest_path: str) -> None:
    """Write a complete, WAL-consistent encrypted copy of the live DB under
    the CURRENT key, using SQLite's online-backup API. Preferred over a raw
    file copy: it captures un-checkpointed WAL frames and never tears pages,
    so it is a trustworthy pre-rekey safety net."""
    key = get_key()
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest_path)
    try:
        _key_pragma(src, key)
        _key_pragma(dst, key)
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def adopt_orphaned_enc_tmp(key: bytes) -> bool:
    """Crash recovery: if a migration crashed between its two swap renames,
    the live app.db is gone but a valid encrypted copy sits at app.db.enc-tmp.
    Adopt it (rename into place) so the next init_db doesn't create an empty
    vault over real data. Returns True if an adoption happened."""
    src = Path(DB_PATH)
    enc_tmp = src.with_name(src.name + ".enc-tmp")
    if src.exists() or not enc_tmp.exists():
        return False
    if not check_key(key, str(enc_tmp)):
        return False
    enc_tmp.replace(src)
    logger.info("Adopted orphaned encrypted DB from an interrupted migration.")
    return True


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
    path = db_path or DB_PATH
    if not Path(path).exists():
        return False
    con = sqlite3.connect(path)
    try:
        con.execute("SELECT count(*) FROM sqlite_master")
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        con.close()


def migrate_plaintext_to_encrypted(key: bytes) -> str:
    """One-shot migration of a plaintext app.db into the vault.

    Order is crash-safe: the encrypted copy is built in a SIDE file first
    (sqlcipher_export from the still-untouched plaintext), verified, and only
    then swapped in; the plaintext original is kept as a .bak file (the user
    deletes it when satisfied). Returns the backup path.
    """
    src = Path(DB_PATH)
    ts = int(time.time())
    enc_tmp = src.with_name(src.name + ".enc-tmp")
    backup = src.with_name(src.name + f".plain.bak-{ts}")
    if enc_tmp.exists():
        enc_tmp.unlink()

    con = sqlite3.connect(str(src))  # unkeyed: reads the plaintext source
    try:
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
        enc_tmp.unlink(missing_ok=True)
        raise RuntimeError("migration_verify_failed")

    # Swap: plaintext → backup, encrypted copy → live path (retry the renames
    # against transient Windows file locks). Order matters for crash recovery:
    # if we crash between the two, adopt_orphaned_enc_tmp() restores enc-tmp on
    # the next unlock. Stale plaintext sidecars are dropped after.
    _rename_with_retry(src, backup)
    _rename_with_retry(enc_tmp, src)
    for suffix in ("-wal", "-shm", "-journal"):
        src.with_name(src.name + suffix).unlink(missing_ok=True)
    logger.info("Plaintext DB migrated into vault; backup at %s", backup.name)
    return str(backup)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

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

