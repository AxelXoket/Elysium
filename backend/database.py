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
        # P4: a full second copy of the vault sitting beside the live one is
        # never allowed to be silent, whether or not we act on it.
        logger.error(
            "An orphaned encrypted DB is present at %s beside a live database "
            "and was NOT adopted. It is a full copy of the vault; it will not "
            "be removed automatically. Inspect both before deleting either.",
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

    enc_tmp.replace(src)
    logger.info(
        "Adopted orphaned encrypted DB from an interrupted migration%s.",
        " (an empty stub was moved aside)" if live_present else "",
    )
    return True


def orphaned_enc_tmp_present() -> bool:
    """True while a full encrypted copy of the vault is stranded on disk.

    Surfaced through /vault/status so an un-adopted orphan is visible in the
    UI rather than only in a log line nobody opens.
    """
    src = Path(DB_PATH)
    return src.with_name(src.name + ".enc-tmp").exists()


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

