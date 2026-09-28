-- Schema 6 as it stood BEFORE the range-reading record (decision 59 a).
-- Built by the base branch's own code (the commit before it, database.init_db),
-- then dumped: every object from sqlite_master, then every row. All rows are
-- synthetic. Loaded into a fresh vault its fingerprint equals
-- test_schema_fingerprint.FINGERPRINT_6_BEFORE_RANGE_READING, which is what
-- proves this file is the previous shape and not a hand-written guess.
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE characters (
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
CREATE TABLE chats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id),
    title        TEXT,
    model_id     TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
, notebook_extracted_ever INTEGER NOT NULL DEFAULT 0, use_global_boundaries INTEGER NOT NULL DEFAULT 1, notebook_auto_accept_override INTEGER, notebook_extracted_ever_reason TEXT CHECK (notebook_extracted_ever_reason IN ('extraction','backfill_done','backfill_notes','backfill_wide','legacy_unknown')));
CREATE TABLE messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL REFERENCES chats(id),
    role       TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
, variant_group INTEGER, active INTEGER NOT NULL DEFAULT 1, truncated INTEGER NOT NULL DEFAULT 0);
CREATE TABLE personas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE attachments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER REFERENCES messages(id),  -- NULL while staged (uploaded, not yet sent)
    sha256     TEXT    NOT NULL,
    mime       TEXT    NOT NULL,
    width      INTEGER NOT NULL,
    height     INTEGER NOT NULL,
    byte_size  INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE attachment_blobs (
    sha256 TEXT PRIMARY KEY,
    data   BLOB NOT NULL
);
CREATE TABLE vault_secrets (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE prompt_module_imports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT    NOT NULL,
    source_filename TEXT    NOT NULL DEFAULT '',
    preset_json     TEXT    NOT NULL DEFAULT '{}',
    report_json     TEXT    NOT NULL DEFAULT '{}',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE prompt_modules (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id             INTEGER REFERENCES prompt_module_imports(id),
    kind                  TEXT    NOT NULL CHECK (kind IN ('module', 'marker')),
    name                  TEXT    NOT NULL,
    content               TEXT    NOT NULL DEFAULT '',
    note                  TEXT    NOT NULL DEFAULT '',
    enabled               INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    sort_order            INTEGER NOT NULL,
    revision              INTEGER NOT NULL DEFAULT 1,
    source_type           TEXT    NOT NULL,
    source_filename       TEXT    NOT NULL DEFAULT '',
    st_identifier         TEXT,
    st_enabled            INTEGER,
    st_role               TEXT,
    st_system_prompt      INTEGER,
    st_marker             INTEGER NOT NULL DEFAULT 0,
    st_injection_position INTEGER,
    st_injection_depth    INTEGER,
    st_injection_order    INTEGER,
    st_injection_trigger  TEXT,
    st_forbid_overrides   INTEGER,
    st_extension          INTEGER,
    after_history         INTEGER NOT NULL DEFAULT 0,
    triggers              TEXT    NOT NULL DEFAULT '[]',
    badges                TEXT    NOT NULL DEFAULT '[]',
    raw_entry_json        TEXT,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE notebook_entries (
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
        , evidence_role TEXT, supersedes_id INTEGER, job_id INTEGER REFERENCES notebook_backfill_jobs(id), unmarked_in_source TEXT);
CREATE TABLE boundaries (
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
        );
CREATE TABLE notebook_extractions (
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
        , attempt_token TEXT);
CREATE TABLE notebook_spend (
          day        TEXT    PRIMARY KEY NOT NULL,
          calls      INTEGER NOT NULL DEFAULT 0,
          tokens_in  INTEGER NOT NULL DEFAULT 0,
          tokens_out INTEGER NOT NULL DEFAULT 0,
          cost       REAL    NOT NULL DEFAULT 0
        , cost_unknown INTEGER NOT NULL DEFAULT 0);
CREATE TABLE notebook_spend_calls (
          call_id     TEXT    PRIMARY KEY NOT NULL,
          day         TEXT    NOT NULL,
          tokens_in   INTEGER NOT NULL DEFAULT 0,
          tokens_out  INTEGER NOT NULL DEFAULT 0,
          cost        REAL,
          created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
CREATE TABLE notebook_backfill_jobs (
          id              INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id         INTEGER NOT NULL REFERENCES chats(id),
          scope           TEXT    NOT NULL
                          CHECK (scope IN ('next','all','range')),
          from_message_id INTEGER,
          to_message_id   INTEGER,
          state           TEXT    NOT NULL
                          CHECK (state IN ('planned','running','stopped',
                                           'done','failed')),
          planned_calls   INTEGER NOT NULL DEFAULT 0,
          spent_calls     INTEGER NOT NULL DEFAULT 0,
          notes_written   INTEGER NOT NULL DEFAULT 0,
          started_at      TEXT,
          finished_at     TEXT,
          last_error      TEXT
        );
CREATE INDEX idx_messages_chat_id ON messages(chat_id);
CREATE INDEX idx_attachments_message_id ON attachments(message_id);
CREATE INDEX idx_prompt_modules_order_v1
    ON prompt_modules(sort_order, id);
CREATE INDEX idx_messages_chat_active ON messages(chat_id, active);
CREATE UNIQUE INDEX idx_one_active_per_group ON messages(variant_group) WHERE variant_group IS NOT NULL AND active = 1;
CREATE INDEX idx_chats_updated_at ON chats(updated_at DESC, id DESC);
CREATE INDEX idx_attachments_sha256 ON attachments(sha256);
CREATE UNIQUE INDEX idx_notebook_order_v1 ON notebook_entries(chat_id, position);
CREATE INDEX idx_notebook_live_v1 ON notebook_entries(chat_id, status, retired_at);
CREATE INDEX idx_boundaries_scope_v1 ON boundaries(scope, chat_id, active);
CREATE INDEX idx_extractions_chat_v1 ON notebook_extractions(chat_id, status, to_message_id);
CREATE INDEX idx_notebook_source_v1 ON notebook_entries(source_message_id);
CREATE INDEX idx_notebook_superseded_v1 ON notebook_entries(superseded_by);
INSERT INTO "settings" (key, value) VALUES ('notebook_ever_flag_amnesty:backfill_wide', '1');
INSERT INTO "settings" (key, value) VALUES ('notebook_auto_accept_backfilled', '1');
INSERT INTO "characters" (id, name, description, personality, scenario, first_mes, mes_example, system_prompt, post_history_instruction, tags, raw_json, created_at) VALUES (1, 'Fixture Character', '', '', '', 'hello', '', '', '', '[]', '{}', '2026-09-27 16:34:45');
INSERT INTO "sqlite_sequence" (name, seq) VALUES ('characters', 1);
INSERT INTO "sqlite_sequence" (name, seq) VALUES ('chats', 2);
INSERT INTO "sqlite_sequence" (name, seq) VALUES ('messages', 7);
INSERT INTO "sqlite_sequence" (name, seq) VALUES ('notebook_backfill_jobs', 1);
INSERT INTO "sqlite_sequence" (name, seq) VALUES ('notebook_entries', 7);
INSERT INTO "chats" (id, character_id, title, model_id, created_at, updated_at, notebook_extracted_ever, use_global_boundaries, notebook_auto_accept_override, notebook_extracted_ever_reason) VALUES (1, 1, 'Fixture chat', NULL, '2026-09-27 16:34:45', '2026-09-27 16:34:45', 0, 1, NULL, NULL);
INSERT INTO "chats" (id, character_id, title, model_id, created_at, updated_at, notebook_extracted_ever, use_global_boundaries, notebook_auto_accept_override, notebook_extracted_ever_reason) VALUES (2, 1, 'Second chat', NULL, '2026-09-27 16:34:45', '2026-09-27 16:34:45', 0, 1, NULL, NULL);
INSERT INTO "messages" (id, chat_id, role, content, created_at, updated_at, variant_group, active, truncated) VALUES (1, 1, 'assistant', 'fixture message 1', '2026-09-01 10:00:00', '2026-09-27 16:34:45', NULL, 1, 0);
INSERT INTO "messages" (id, chat_id, role, content, created_at, updated_at, variant_group, active, truncated) VALUES (2, 1, 'user', 'fixture message 2', '2026-09-01 10:00:00', '2026-09-27 16:34:45', NULL, 1, 0);
INSERT INTO "messages" (id, chat_id, role, content, created_at, updated_at, variant_group, active, truncated) VALUES (3, 1, 'assistant', 'fixture message 3', '2026-09-01 10:00:00', '2026-09-27 16:34:45', NULL, 1, 0);
INSERT INTO "messages" (id, chat_id, role, content, created_at, updated_at, variant_group, active, truncated) VALUES (4, 1, 'user', 'fixture message 4', '2026-09-01 10:00:00', '2026-09-27 16:34:45', NULL, 1, 0);
INSERT INTO "messages" (id, chat_id, role, content, created_at, updated_at, variant_group, active, truncated) VALUES (5, 1, 'assistant', 'fixture message 5', '2026-09-01 10:00:00', '2026-09-27 16:34:45', NULL, 1, 0);
INSERT INTO "messages" (id, chat_id, role, content, created_at, updated_at, variant_group, active, truncated) VALUES (6, 1, 'user', 'fixture message 6', '2026-09-01 10:00:00', '2026-09-27 16:34:45', NULL, 1, 0);
INSERT INTO "messages" (id, chat_id, role, content, created_at, updated_at, variant_group, active, truncated) VALUES (7, 2, 'user', 'other chat', '2026-09-01 10:00:00', '2026-09-27 16:34:45', NULL, 1, 0);
INSERT INTO "notebook_entries" (id, chat_id, position, kind, text, evidence, durability, importance, pinned, retired_at, superseded_by, excluded_reason, status, provenance, source_message_id, created_at, updated_at, evidence_role, supersedes_id, job_id, unmarked_in_source) VALUES (1, 1, 0, 'fact', 'A note the reader typed.', NULL, 'permanent', 2, 0, NULL, NULL, NULL, 'accepted', 'user', NULL, '2026-09-01 10:00:00', '2026-09-01 10:00:00', NULL, NULL, NULL, NULL);
INSERT INTO "notebook_entries" (id, chat_id, position, kind, text, evidence, durability, importance, pinned, retired_at, superseded_by, excluded_reason, status, provenance, source_message_id, created_at, updated_at, evidence_role, supersedes_id, job_id, unmarked_in_source) VALUES (2, 1, 1, 'fact', 'A proposal nobody looked at.', NULL, 'permanent', 2, 0, NULL, NULL, NULL, 'proposed', 'model', 3, '2026-09-01 10:00:00', '2026-09-01 10:00:00', NULL, NULL, NULL, NULL);
INSERT INTO "notebook_entries" (id, chat_id, position, kind, text, evidence, durability, importance, pinned, retired_at, superseded_by, excluded_reason, status, provenance, source_message_id, created_at, updated_at, evidence_role, supersedes_id, job_id, unmarked_in_source) VALUES (3, 1, 2, 'fact', 'An auto-accepted model note.', NULL, 'permanent', 2, 0, NULL, NULL, NULL, 'accepted', 'model', 3, '2026-09-01 10:00:00', '2026-09-01 10:00:00', NULL, NULL, NULL, NULL);
INSERT INTO "notebook_entries" (id, chat_id, position, kind, text, evidence, durability, importance, pinned, retired_at, superseded_by, excluded_reason, status, provenance, source_message_id, created_at, updated_at, evidence_role, supersedes_id, job_id, unmarked_in_source) VALUES (4, 1, 3, 'fact', 'A model note somebody touched.', NULL, 'permanent', 2, 1, NULL, NULL, NULL, 'accepted', 'model', 5, '2026-09-01 10:00:00', '2026-09-02 11:00:00', NULL, NULL, 1, NULL);
INSERT INTO "notebook_entries" (id, chat_id, position, kind, text, evidence, durability, importance, pinned, retired_at, superseded_by, excluded_reason, status, provenance, source_message_id, created_at, updated_at, evidence_role, supersedes_id, job_id, unmarked_in_source) VALUES (5, 1, 4, 'fact', 'A retired model note.', NULL, 'permanent', 2, 0, '2026-09-02 11:00:00', 6, NULL, 'accepted', 'model', 5, '2026-09-01 10:00:00', '2026-09-01 10:00:00', NULL, NULL, NULL, NULL);
INSERT INTO "notebook_entries" (id, chat_id, position, kind, text, evidence, durability, importance, pinned, retired_at, superseded_by, excluded_reason, status, provenance, source_message_id, created_at, updated_at, evidence_role, supersedes_id, job_id, unmarked_in_source) VALUES (6, 1, 5, 'fact', 'Its replacement.', NULL, 'permanent', 2, 0, NULL, NULL, NULL, 'accepted', 'model', 5, '2026-09-01 10:00:00', '2026-09-01 10:00:00', NULL, NULL, NULL, NULL);
INSERT INTO "notebook_entries" (id, chat_id, position, kind, text, evidence, durability, importance, pinned, retired_at, superseded_by, excluded_reason, status, provenance, source_message_id, created_at, updated_at, evidence_role, supersedes_id, job_id, unmarked_in_source) VALUES (7, 2, 0, 'fact', 'Other chat note.', NULL, 'permanent', 2, 0, NULL, NULL, NULL, 'accepted', 'user', NULL, '2026-09-01 10:00:00', '2026-09-01 10:00:00', NULL, NULL, NULL, NULL);
INSERT INTO "notebook_extractions" (work_key, chat_id, from_message_id, to_message_id, status, started_at, request_id, finish_reason, skip_reason, error_type, tokens_in, tokens_out, cost, created_at, attempt_token) VALUES ('wk-done-1', 1, 1, 3, 'done', '2026-09-01 10:00:00', NULL, NULL, NULL, NULL, NULL, NULL, NULL, '2026-09-01 10:00:00', NULL);
INSERT INTO "notebook_extractions" (work_key, chat_id, from_message_id, to_message_id, status, started_at, request_id, finish_reason, skip_reason, error_type, tokens_in, tokens_out, cost, created_at, attempt_token) VALUES ('wk-done-2', 1, 4, 6, 'done', '2026-09-01 10:00:00', NULL, NULL, NULL, NULL, NULL, NULL, NULL, '2026-09-01 10:00:00', NULL);
INSERT INTO "notebook_extractions" (work_key, chat_id, from_message_id, to_message_id, status, started_at, request_id, finish_reason, skip_reason, error_type, tokens_in, tokens_out, cost, created_at, attempt_token) VALUES ('wk-failed', 2, 7, 7, 'failed', '2026-09-01 10:00:00', NULL, NULL, NULL, NULL, NULL, NULL, NULL, '2026-09-01 10:00:00', NULL);
INSERT INTO "notebook_backfill_jobs" (id, chat_id, scope, from_message_id, to_message_id, state, planned_calls, spent_calls, notes_written, started_at, finished_at, last_error) VALUES (1, 1, 'range', 1, 6, 'done', 1, 1, 1, '2026-09-01 10:00:00', '2026-09-01 10:00:00', NULL);
PRAGMA user_version = 6;
