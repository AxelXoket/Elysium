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
import notebook_store as notebook


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
        this ever goes red, every limit written once disappears on unlock."""
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
        would break the rule that a note never disappears. So they move.
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


class TestTheSelfHealActuallyConverges:
    """Two independent reviews reproduced the same permanent lockout.

    The first version added a flat +100000 to every loser, which turns N rows
    sharing a position into N-1 rows sharing a NEW one. The unique index then
    aborts, `init_db` has no error path, and the vault never opens again -
    identically on every subsequent unlock. The original test constructed the
    only shape that happened to survive: two rows, no row already sitting at
    the shifted number.
    """

    def _plant(self, con, chat: int, n: int, at: int = 7) -> None:
        con.execute("DROP INDEX IF EXISTS idx_notebook_order_v1")
        for i in range(n):
            con.execute(
                "INSERT INTO notebook_entries (chat_id, position, text) "
                "VALUES (?, ?, ?)", (chat, at, f"dupe{i}"))

    def test_three_rows_on_one_position(self, db) -> None:
        with database.get_db() as con:
            chat = _a_chat(con)
            self._plant(con, chat, 3)

        database.init_db()          # must not raise

        with database.get_db() as con:
            rows = con.execute(
                "SELECT text, position FROM notebook_entries "
                "WHERE chat_id = ?", (chat,)).fetchall()
        assert len(rows) == 3, "a note was deleted"
        assert len({r["position"] for r in rows}) == 3, "still colliding"

    def test_a_loser_does_not_land_on_an_occupied_slot(self, db) -> None:
        """The other shape that aborted: a row already sitting where the
        shifted one would go."""
        with database.get_db() as con:
            chat = _a_chat(con)
            con.execute(
                "INSERT INTO notebook_entries (chat_id, position, text) "
                "VALUES (?, 100007, 'already there')", (chat,))
            self._plant(con, chat, 2)

        database.init_db()

        with database.get_db() as con:
            positions = [r[0] for r in con.execute(
                "SELECT position FROM notebook_entries WHERE chat_id = ?",
                (chat,)).fetchall()]
        assert len(positions) == len(set(positions)) == 3

    def test_two_chats_do_not_renumber_each_other(self, db) -> None:
        with database.get_db() as con:
            a = _a_chat(con)
            b = _a_chat(con)
            self._plant(con, a, 2)
            self._plant(con, b, 2)
        database.init_db()
        with database.get_db() as con:
            for chat in (a, b):
                pos = [r[0] for r in con.execute(
                    "SELECT position FROM notebook_entries WHERE chat_id = ?",
                    (chat,)).fetchall()]
                assert len(pos) == len(set(pos)) == 2


class TestALimitCannotGrowBigEnoughToStopTheApp:
    """The ceiling on a limit, and the arithmetic that picked the number.

    Limits are the ONE block that is never trimmed, and that is correct: a
    limit the user believes is in force must never be silently dropped. The
    price of the promise is that a long enough limit does not degrade the
    prompt, it ENDS it - every send in the chat fails with
    `boundaries_do_not_fit`, permanently, and the only cure is in a panel
    nobody is looking at. Notes were capped for exactly this reason; limits
    were missed.

    So: a positive control on both ceilings, and the two that matter most -
    the cap must not make an existing set unremovable, and a set filled to the
    ceiling must actually fit the model the ceiling was computed for.
    """

    def _limit(self, chat=None, n=None):
        n = notebook.BOUNDARY_MAX_CHARS if n is None else n
        return notebook.create_boundary(
            "L" * min(n, notebook.BOUNDARY_MAX_CHARS), "p" * n,
            "hard", chat_id=chat)

    def test_a_limit_at_the_cap_is_accepted(self, db) -> None:
        """The positive control. A ceiling nothing can reach is a ban."""
        row = self._limit()
        assert len(row["phrasing"]) == notebook.BOUNDARY_MAX_CHARS
        assert len(row["label"]) == notebook.BOUNDARY_MAX_CHARS

    def test_one_character_over_the_cap_is_refused(self, db) -> None:
        n = notebook.BOUNDARY_MAX_CHARS + 1
        with pytest.raises(notebook.NotebookError) as exc:
            notebook.create_boundary("ok", "p" * n, "hard")
        assert exc.value.code == "boundary_too_long"

        # The LABEL too. It never reaches the prompt, but an unbounded column
        # is an unbounded column and the panel has to render it.
        with pytest.raises(notebook.NotebookError) as exc:
            notebook.create_boundary("L" * n, "ok", "hard")
        assert exc.value.code == "boundary_too_long"

    def test_the_cap_is_the_domains_and_not_the_routes(self, db, client) -> None:
        """Through HTTP, because the UI cap is not enforcement.

        `BoundaryBody` declares no max_length on purpose - with one, this would
        arrive as a 422 validation structure instead of the catalogued code,
        and the reader would get "Something went wrong" for a refusal that has
        a sentence written for it.
        """
        r = client.post(
            "/api/v1/notebook/boundaries",
            json={"label": "ok", "severity": "hard",
                  "phrasing": "p" * (notebook.BOUNDARY_MAX_CHARS + 1)})
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "boundary_too_long"

    def test_the_line_cost_still_matches_the_line_that_is_built(self, db) -> None:
        """The constant the whole budget is divided by, measured rather than
        trusted. If a severity word or the bullet ever gets longer the
        arithmetic behind BOUNDARY_MAX_CHARS is quietly wrong, and the block
        starts overrunning the budget it was sized for with nothing saying so.
        """
        # FOUR dimensions, not two. The line grew a tail - what to do when
        # the limit is crossed, and the rating ceiling - and a loop that only
        # walked severity and polarity would have kept reporting the old
        # worst case while the real one was five times longer. That is
        # exactly the silent budget overrun this test exists to prevent,
        # arriving through the test rather than the code.
        worst = 0
        for severity in notebook.SEVERITIES:
            for polarity in ("avoid", "seek"):
                for action in notebook._ON_VIOLATION_PROSE:
                    for rating in (None, *notebook._RATING_PROSE):
                        line = notebook._boundary_line(
                            {"phrasing": "", "severity": severity,
                             "polarity": polarity, "on_violation": action,
                             "rating_ceiling": rating})
                        worst = max(worst, len(line) + 1)
        assert worst == notebook._BOUNDARY_LINE_COST

    def test_a_line_carries_what_to_do_about_the_limit(self, db) -> None:
        """The setting that was collected, validated, stored, shown back to
        the person who set it - and never sent. Every value has to reach the
        model, or the panel is displaying a promise the app does not keep."""
        for action, prose in notebook._ON_VIOLATION_PROSE.items():
            line = notebook._boundary_line(
                {"phrasing": "no gore", "severity": "hard",
                 "polarity": "avoid", "on_violation": action,
                 "rating_ceiling": None})
            if prose:
                assert prose in line, action
            else:
                # `pause` is what a limit means already; saying it on every
                # line would spend the block's budget on "behave normally".
                assert line == "- (never) no gore"

    def test_a_line_carries_the_rating_ceiling(self, db) -> None:
        for rating, prose in notebook._RATING_PROSE.items():
            line = notebook._boundary_line(
                {"phrasing": "no gore", "severity": "hard",
                 "polarity": "avoid", "on_violation": "pause",
                 "rating_ceiling": rating})
            assert prose in line, rating

    def test_a_line_with_neither_is_unchanged(self, db) -> None:
        """GROUND CONTROL. The ordinary limit - default action, no rating -
        must read exactly as it always did, or every existing block just got
        longer for nothing."""
        line = notebook._boundary_line(
            {"phrasing": "no gore", "severity": "soft", "polarity": "seek",
             "on_violation": "pause", "rating_ceiling": None})
        assert line == "- (seek) no gore"

    def test_the_set_is_capped_and_not_only_each_limit(self, db) -> None:
        """Eight limits at the cap is a large block; eighty is a broken app,
        and the route can be called eighty times."""
        made = 0
        while True:
            try:
                self._limit()
            except notebook.NotebookError as exc:
                assert exc.code == "boundary_set_too_long"
                break
            made += 1
            assert made < 100, "the set never refused - it has no ceiling"
        # The arithmetic as a number rather than as a comment: 1500 characters
        # of line budget, 181 per limit written at the cap.
        assert made == notebook.BOUNDARY_SET_MAX_CHARS // (
            notebook.BOUNDARY_MAX_CHARS + notebook._BOUNDARY_LINE_COST)

    def test_a_short_limit_still_goes_in_beside_a_full_set(self, db) -> None:
        """The positive control for the set ceiling: it refuses what does not
        fit, not everything arriving after a lot of text.

        The count is DERIVED. It was a literal 6, which was one less than the
        capacity at the time; the line cost has since grown and six no longer
        leaves room for anything. A number that has to be edited whenever the
        arithmetic moves is a number that will be edited to whatever makes
        the test pass.
        """
        capacity = notebook.BOUNDARY_SET_MAX_CHARS // (
            notebook.BOUNDARY_MAX_CHARS + notebook._BOUNDARY_LINE_COST)
        for _ in range(capacity - 1):
            self._limit()
        row = notebook.create_boundary("no gore", "Avoid graphic injury.",
                                       "hard")
        assert row["id"]

    def test_a_chat_limit_is_measured_beside_the_global_set(self, db) -> None:
        """What a chat is sent is the global set PLUS its own, so a chat limit
        that fits only while the globals are ignored is a chat that breaks the
        moment somebody turns them back on."""
        with database.get_db() as con:
            chat = _a_chat(con)
        while True:
            try:
                self._limit()
            except notebook.NotebookError:
                break
        with pytest.raises(notebook.NotebookError) as exc:
            self._limit(chat=chat)
        assert exc.value.code == "boundary_set_too_long"

    def test_an_over_length_limit_can_still_be_deleted(self, db) -> None:
        """The cure must stay reachable.

        A limit written before the ceiling existed - or by any writer that did
        not come through create_boundary - is exactly the row somebody needs to
        get rid of. A rule that also blocked the delete would be the same "the
        app is stuck and the fix is out of reach" failure it was written to
        prevent, arriving from the other side.
        """
        with database.get_db() as con:
            con.execute(
                "INSERT INTO boundaries (scope, label, phrasing, severity) "
                "VALUES ('global', 'old', ?, 'hard')",
                ("p" * (notebook.BOUNDARY_MAX_CHARS * 10),))
            old = con.execute("SELECT MAX(id) FROM boundaries").fetchone()[0]

        # It is readable, it is in force, and it is deletable.
        assert any(r["id"] == old for r in notebook.list_boundaries())
        assert notebook.delete_boundary(old) is True
        assert not any(r["id"] == old for r in notebook.list_boundaries())

    def test_a_full_set_fits_the_model_the_ceiling_was_sized_for(self, db):
        """The payoff, and the one test that checks the NUMBER rather than the
        rule.

        completions.py refuses the turn when the boundary block plus the user's
        message will not fit `available`. On the smallest model this app serves
        - 8k window, 256 tokens of safety margin, three characters to the
        token, 2048 reserved for the reply - that is 17664 characters, and the
        limits are given the same tenth of it the notebook takes. A set filled
        to its ceiling has to land inside that tenth or the ceiling is
        decorative.
        """
        import config
        with database.get_db() as con:
            chat = _a_chat(con)
        while True:
            try:
                self._limit()
            except notebook.NotebookError:
                break

        available = ((8192 - config.CONTEXT_SAFETY_MARGIN)
                     * config.CHARS_PER_TOKEN_ESTIMATE
                     - 2048 * config.CHARS_PER_TOKEN_ESTIMATE)
        block = notebook.build_boundary_block(chat)
        assert block, "nothing was assembled - the test measured an empty set"
        assert len(block) <= available * notebook.NOTEBOOK_BUDGET_FRACTION
