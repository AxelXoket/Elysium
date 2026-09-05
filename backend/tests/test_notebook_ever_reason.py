"""WHY the "this chat has been read" flag is lit, and what may be done about it.

`chats.notebook_extracted_ever` is one bit with two histories behind it, and
until this column the app could not tell them apart:

  (X) the chat really was read, and `forget_proposals_from_messages` later
      deleted the extraction rows that proved it. Clearing the flag sends a
      long chat back to its oldest message, twenty at a time.
  (Y) nothing was ever read. The shipped writer was unconditional and the
      shipped backfill lit the flag from any extraction row at all, so a
      `skipped`, a `failed` or a `running` row was enough. Leaving the flag
      costs about one billed call per twenty messages, from the oldest end,
      forever, with nothing in the app able to clear it.

Every test here is measured on the PLAN the worker would make, not on the
presence of a column. A schema assertion cannot go red for the reason that
matters, and the reason that matters is money and a notebook that lags a
session behind.

Four named mutations, each of which this file must turn red:

  M1 TEMIZLIK_ATES_ETMEDI      the amnesty does not fire, a suspect flag stays
  M2 TEMIZLIK_FAZLA_SILDI      the amnesty over-reaches and takes a correct one
  M3 YAZAR_ETIKETLEMIYOR       the writer stops upgrading a migrated label, so
                               the set nobody can vouch for freezes forever
  M4 SURUM_DAMGASI_KALMADI     the version stamp does not move, so an older
                               build opens this file and writes unlabeled rows
"""
from __future__ import annotations

import hashlib

import pytest

import config
import database
import notebook_store as notebook
import notebook_worker
from database import get_db

from tests.test_notebook_store_accounting import (
    ever_flag, message_ids, seed, settle)

#: The planner's two arguments, fixed here so every claim below is about the
#: same world: a turn threshold of 4 (so the jump branch needs more than 8
#: pending) and a batch of 20.
THRESHOLD, BATCH = 4, 20

#: The last schema version that shipped WITHOUT this column. A historical
#: fact, not a copy of a live constant: it is what a `v1.1.6` build believes
#: about the file it is opening.
VERSION_BEFORE_THE_REASON_COLUMN = 5


def fact(n: int = 0) -> dict:
    return {"kind": "fact", "text": f"note {n}: her brother owns the mill",
            "durability": "session", "importance": 2, "evidence": None}


def reason_of(chat_id: int):
    with get_db() as con:
        return con.execute(
            "SELECT notebook_extracted_ever_reason FROM chats WHERE id = ?",
            (chat_id,)).fetchone()[0]


def set_state(chat_id: int, flag: int, reason: str | None) -> None:
    """Put a chat into a state only history could have produced.

    Written by hand on purpose: no build writes `backfill_wide`, and the
    whole point of the label is to describe databases this build never made.
    """
    with get_db() as con:
        con.execute(
            "UPDATE chats SET notebook_extracted_ever = ?, "
            "notebook_extracted_ever_reason = ? WHERE id = ?",
            (flag, reason, chat_id))


def forget_the_reading_record(chat_id: int) -> None:
    """What an edit near the start of a chat does to the extraction rows."""
    with get_db() as con:
        con.execute("DELETE FROM notebook_extractions WHERE chat_id = ?",
                    (chat_id,))


def forget_the_notes(chat_id: int) -> None:
    with get_db() as con:
        con.execute("DELETE FROM notebook_entries WHERE chat_id = ?",
                    (chat_id,))


def really_read(chat_id: int, ids: list[int], ranges: int = 3) -> None:
    """`ranges` completed extractions through the REAL writer, with notes."""
    for r in range(ranges):
        settle(chat_id, work_key=f"read-{chat_id}-{r}",
               from_id=ids[r * BATCH], to_id=ids[(r + 1) * BATCH - 1],
               proposals=[fact(r)])


def plan(chat_id: int):
    """The planner, with the edge the WORKER passes.

    `first_read_edge` defaults to `threshold * 2` inside `_plan_work`, which
    would put these measurements in a world the app never runs in. The worker
    passes `config.NOTEBOOK_EXTRACT_EVERY_TURNS * 2`
    (`notebook_worker.py`, `_prepare`), so that is what these tests pass -
    imported, not retyped.
    """
    return notebook_worker._plan_work(
        chat_id, "vendor/cheap", "en", THRESHOLD, BATCH,
        first_read_edge=config.NOTEBOOK_EXTRACT_EVERY_TURNS * 2)


def unmigrate_the_reason_column() -> None:
    """Put the database back in the shape that upgrades INTO this column.

    `DROP COLUMN` rather than a hand-written pre-migration schema, for the
    reason its sibling in test_notebook_store_accounting.py gives: the thing
    under test is the REAL `chats` table minus exactly one column, and a
    copied CREATE would drift away from it in silence.

    The assertion is the ground control for every test that calls this. With
    the column still present, `_migrate`'s guard skips the ALTER and the
    labels read afterwards are just whatever was already there.
    """
    with get_db() as con:
        con.execute(
            "ALTER TABLE chats DROP COLUMN notebook_extracted_ever_reason")
        cols = {r[1] for r in
                con.execute("PRAGMA table_info(chats)").fetchall()}
    assert "notebook_extracted_ever_reason" not in cols, (
        "the column survived the drop, so the migration under test never ran")


def amnesty_key_cleared(labels=database._AMNESTY_SAFE_LABELS) -> None:
    """Forget that this label set's one-time pardon was ever spent."""
    with get_db() as con:
        con.execute("DELETE FROM settings WHERE key = ?",
                    (database._amnesty_key(labels),))


def amnesty_spent(labels=database._AMNESTY_SAFE_LABELS) -> bool:
    with get_db() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?",
                          (database._amnesty_key(labels),)).fetchone()
    return row is not None and row[0] == "1"


def count_chat_writes() -> int:
    with get_db() as con:
        return con.execute("SELECT COUNT(*) FROM _chat_writes").fetchone()[0]


def watch_chat_writes() -> None:
    """Count every UPDATE the production writer makes to `chats`.

    A TRIGGER, not a copy of the statement. The claim under test is about
    how often the real writer writes, and the only honest way to measure
    that is to let the real writer run and have the database tell us. This
    schema has no triggers of its own, so nothing else can fire it, and it
    dies with the temporary database.
    """
    with get_db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS _chat_writes (at TEXT)")
        con.execute("DROP TRIGGER IF EXISTS _watch_chat_writes")
        con.execute(
            "CREATE TRIGGER _watch_chat_writes AFTER UPDATE ON chats "
            "BEGIN INSERT INTO _chat_writes (at) VALUES (datetime('now')); END")


def run_amnesty(labels=database._AMNESTY_SAFE_LABELS) -> int:
    with get_db() as con:
        return database._run_ever_flag_amnesty(con, labels)


# ---------------------------------------------------------------------------
# (a) the column arrives and says what it can prove
# ---------------------------------------------------------------------------
class TestTheMigrationSaysWhyEachFlagIsLit:
    def test_a_done_row_on_disk_is_named_as_the_reason(self, client) -> None:
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="lbl-done", from_id=ids[0], to_id=ids[19])

        unmigrate_the_reason_column()
        database.init_db()

        assert ever_flag(chat_id) == 1, (
            "ground: the flag the label is about is not even lit")
        assert reason_of(chat_id) == database._EVER_REASON_BACKFILL_DONE

    def test_a_surviving_model_note_rescues_a_chat_from_the_unknown_set(
            self, client) -> None:
        """THE SECOND SIGNAL, and the only reason the unknown set is smaller
        than the whole ambiguous one.

        `provenance='model'` is written in exactly one place -
        `commit_extraction`'s proposal INSERT - and the only caller that
        reaches it settles with `status='done'`. So a model note standing in
        a chat is proof that a completed extraction once landed there, even
        after every extraction row has been deleted. One-way proof: its
        presence proves, its absence proves nothing, which is why the label
        it produces is separate from `backfill_done` rather than merged with
        it.
        """
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="lbl-notes", from_id=ids[0], to_id=ids[19],
               proposals=[fact()])
        forget_the_reading_record(chat_id)

        unmigrate_the_reason_column()
        database.init_db()

        with get_db() as con:
            notes = con.execute(
                "SELECT COUNT(*) FROM notebook_entries WHERE chat_id = ? "
                "AND provenance = 'model'", (chat_id,)).fetchone()[0]
        assert notes == 1, (
            "ground: the note this label is derived from is not on disk")
        assert reason_of(chat_id) == database._EVER_REASON_BACKFILL_NOTES

    def test_a_note_the_reader_never_reviewed_still_counts(
            self, client) -> None:
        """POSITIVE CONTROL against a filtered discriminator.

        On a vault with auto-accept off, every model note sits at
        `proposed`. Measured on a synthetic fixture: a discriminator written
        as `... AND status = 'accepted'` finds nothing at all in a chat that
        was demonstrably read, and the chat drops into the unknown set. A
        note the reader has not looked at is still evidence that a reading
        happened.
        """
        import config
        database.set_setting(config.SETTING_NOTEBOOK_AUTO_ACCEPT, "0")
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="lbl-proposed", from_id=ids[0],
               to_id=ids[19], proposals=[fact()])
        forget_the_reading_record(chat_id)

        unmigrate_the_reason_column()
        database.init_db()

        with get_db() as con:
            statuses = {r[0] for r in con.execute(
                "SELECT status FROM notebook_entries WHERE chat_id = ?",
                (chat_id,)).fetchall()}
        assert statuses == {notebook.STATUS_PROPOSED}, (
            "ground: the note is not in the state this test is about")
        assert reason_of(chat_id) == database._EVER_REASON_BACKFILL_NOTES

    def test_a_note_the_READER_wrote_proves_nothing(self, client) -> None:
        """THE DISCRIMINATING HALF, and without it the second signal is not a
        signal at all.

        The whole claim of `backfill_notes` is that a MODEL note proves a
        completed extraction landed here. A note the reader typed themselves
        proves only that they typed it - they can write in a notebook for a
        chat the extractor has never touched. A labeller that looked for any
        note at all would call that chat read, and the amnesty would then
        refuse to clear a flag nothing justifies.
        """
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="user-note", from_id=ids[0], to_id=ids[19],
               status="skipped", skip_reason="proxy_gate")
        set_state(chat_id, 1, None)
        notebook.create_entry(
            chat_id, "I think her brother owns the mill",
            kind="fact", durability="session", importance=2)
        with get_db() as con:
            written = con.execute(
                "SELECT provenance FROM notebook_entries WHERE chat_id = ?",
                (chat_id,)).fetchone()[0]
        assert written == notebook.PROV_USER, (
            "ground: the note this test is about was not stored as the "
            "reader's own")

        unmigrate_the_reason_column()
        database.init_db()

        assert reason_of(chat_id) == database._EVER_REASON_LEGACY_UNKNOWN, (
            "a note the reader typed was read as evidence that the extractor "
            "had been through this chat")

    def test_a_chat_whose_notes_went_through_retirement_still_counts(
            self, client) -> None:
        """The OTHER filter the labeller refuses, and the honest limit of
        what can be shown about it.

        `retire_superseded` sets `retired_at` rather than deleting, so a
        `... AND retired_at IS NULL` discriminator would look past a retired
        note. WHETHER THAT COULD EVER LOSE THE LAST PIECE OF EVIDENCE WAS
        MEASURED, and the answer is no on today's code: a retired note never
        outlives its superseder, because both the sweep
        (`forget_proposals_from_messages`) and `delete_entry` clear
        `retired_at` and `superseded_by` on the rows pointing at whatever
        they remove. Asserting an unreachable state would be fiction, so this
        asserts the reachable one: a chat whose notes have been retired,
        superseded and swept still comes out labelled from its notes.

        The filter stays out of the query as defence, not as a fix for a
        defect that exists today, and the docstring in `database.py` says so
        in the same words.
        """
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="retired-note", from_id=ids[0],
               to_id=ids[19], proposals=[fact(0)])
        settle(chat_id, work_key="retired-note-2", from_id=ids[20],
               to_id=ids[29], proposals=[fact(1)])
        with get_db() as con:
            rows = [r[0] for r in con.execute(
                "SELECT id FROM notebook_entries WHERE chat_id = ? ORDER BY id",
                (chat_id,)).fetchall()]
            moved = notebook.retire_superseded(con, chat_id, rows[0], rows[1])
            # The sweep takes the superseding note's own message with it.
            notebook.forget_proposals_from_messages(con, [ids[29]])
            surviving = con.execute(
                "SELECT COUNT(*) FROM notebook_entries WHERE chat_id = ? "
                "AND provenance = 'model'", (chat_id,)).fetchone()[0]
        assert moved == 1, (
            "ground: the retirement this test is about never happened")
        assert surviving == 1, (
            "ground: the sweep left a different number of notes than this "
            "test reasons about")
        forget_the_reading_record(chat_id)

        unmigrate_the_reason_column()
        database.init_db()

        assert reason_of(chat_id) == database._EVER_REASON_BACKFILL_NOTES

    def test_a_flag_with_nothing_behind_it_says_so(self, client) -> None:
        """The (Y) case: a `skipped` row lit the flag on a shipped build and
        there is nothing left on disk to justify it."""
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="lbl-skip", from_id=ids[0], to_id=ids[19],
               status="skipped", skip_reason="proxy_gate")
        set_state(chat_id, 1, None)   # the shipped writer was unconditional

        unmigrate_the_reason_column()
        database.init_db()

        assert reason_of(chat_id) == database._EVER_REASON_LEGACY_UNKNOWN

    def test_a_dark_flag_claims_nothing(self, client) -> None:
        """GROUND CONTROL for all four above. A migration that labelled
        every row would satisfy them and would be asserting a reading for
        chats nothing has ever touched."""
        chat_id = seed(client, 30)

        unmigrate_the_reason_column()
        database.init_db()

        assert ever_flag(chat_id) == 0
        assert reason_of(chat_id) is None

    def test_the_column_refuses_a_label_nobody_declared(self, client) -> None:
        """The CHECK is live, so a typo is a loud failure and not a sixth
        category nothing knows how to read."""
        chat_id = seed(client, 4)
        import sqlite3 as _std
        with get_db() as con:
            with pytest.raises((_std.IntegrityError, Exception)):
                con.execute(
                    "UPDATE chats SET notebook_extracted_ever_reason = "
                    "'extracton' WHERE id = ?", (chat_id,))

    def test_a_crash_between_the_alter_and_the_labels_is_healed(
            self, client) -> None:
        """The reason the labelling sits OUTSIDE the column's own `if`.

        The ALTER fires once, on one launch, and never again. A crash after
        it and before the labels would leave rows that no later launch could
        ever explain - the same trap the flag's own migration fell into and
        the one this repair exists to get out of.
        """
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="heal-done", from_id=ids[0], to_id=ids[19])
        # The launch that added the column and then died.
        unmigrate_the_reason_column()
        with get_db() as con:
            con.execute("ALTER TABLE chats ADD COLUMN "
                        "notebook_extracted_ever_reason TEXT")
        assert reason_of(chat_id) is None, (
            "ground: the crash state this test is about was not reached")

        database.init_db()

        assert reason_of(chat_id) == database._EVER_REASON_BACKFILL_DONE

    def test_the_healing_pass_never_touches_the_flag(self, client) -> None:
        """R3.2: a repair that runs on every launch must not re-decide the
        flag, or it re-opens the defect it was written to close.

        A chat the amnesty already cleared is the sharpest case: it is dark,
        it has no label, and there is a `done`-less extraction row on disk.
        If the labelling pass had any opinion about the flag, the next launch
        would light it again and the pardon would be undone in silence.
        """
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="untouched", from_id=ids[0], to_id=ids[19],
               status="failed", error_type="ReadTimeout")
        set_state(chat_id, 0, None)

        database.init_db()
        database.init_db()

        assert ever_flag(chat_id) == 0, (
            "the labelling pass re-lit a flag it has no business deciding")
        assert reason_of(chat_id) is None

    def test_running_the_migration_twice_changes_nothing(
            self, client) -> None:
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="idem", from_id=ids[0], to_id=ids[19])
        unmigrate_the_reason_column()
        database.init_db()
        first = (ever_flag(chat_id), reason_of(chat_id))

        database.init_db()

        assert (ever_flag(chat_id), reason_of(chat_id)) == first

    def test_a_migrated_label_is_not_overwritten_by_a_weaker_one(
            self, client) -> None:
        """The three statements are ordered and each only speaks where
        nobody has. A chat the WRITER labelled must not be downgraded to a
        migration's guess on the next launch."""
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="strong", from_id=ids[0], to_id=ids[19])
        assert reason_of(chat_id) == database._EVER_REASON_EXTRACTION, (
            "ground: the writer did not label it in the first place")
        forget_the_reading_record(chat_id)

        database.init_db()

        assert reason_of(chat_id) == database._EVER_REASON_EXTRACTION


# ---------------------------------------------------------------------------
# (b) the writer records first-hand evidence
# ---------------------------------------------------------------------------
class TestTheWriterRecordsWhy:
    def test_a_real_extraction_says_it_read_the_chat_itself(
            self, client) -> None:
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="w-done", from_id=ids[0], to_id=ids[19])

        assert ever_flag(chat_id) == 1
        assert reason_of(chat_id) == database._EVER_REASON_EXTRACTION

    @pytest.mark.parametrize("status,error_type,skip_reason", [
        ("skipped", None, "proxy_gate"),
        ("failed", "ReadTimeout", None),
        ("running", None, None),
    ])
    def test_an_attempt_that_read_nothing_claims_nothing(
            self, client, status, error_type, skip_reason) -> None:
        """GROUND CONTROL for the whole class. A writer that labelled every
        settle would pass the test above and would be writing first-hand
        evidence for calls that never returned a line."""
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key=f"w-{status}", from_id=ids[0], to_id=ids[19],
               status=status, error_type=error_type, skip_reason=skip_reason)

        assert ever_flag(chat_id) == 0
        assert reason_of(chat_id) is None


# ---------------------------------------------------------------------------
# M1 / M2 - the amnesty, measured on the plan
# ---------------------------------------------------------------------------
class TestTheAmnestyClearsWhatNothingVouchesFor:
    """M1 TEMIZLIK_ATES_ETMEDI and M2 TEMIZLIK_FAZLA_SILDI.

    Both claims are about the PLAN. `_plan_work` reads the flag only when the
    cursor has not moved, and what it decides is which end of a two hundred
    message chat gets paid for: the newest twenty, once, or the oldest twenty
    and then the next twenty and then the next.
    """

    def unvouched_chat(self, client) -> tuple[int, list[int]]:
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        settle(chat_id, work_key=f"amn-{chat_id}", from_id=ids[0],
               to_id=ids[19], status="skipped", skip_reason="proxy_gate")
        set_state(chat_id, 1, database._EVER_REASON_BACKFILL_WIDE)
        return chat_id, ids

    def test_before_the_amnesty_the_suspect_chat_reads_from_its_oldest_end(
            self, client) -> None:
        """GROUND CONTROL for M1. Without it the test below passes in a world
        where nothing was ever wrong."""
        chat_id, ids = self.unvouched_chat(client)

        p = plan(chat_id)

        assert p is not None
        assert p["from_id"] == ids[0], (
            "the state this repair is for was not reached")

    def test_the_amnesty_sends_an_unvouched_chat_back_to_the_present(
            self, client) -> None:
        """M1. Mutant: the amnesty does not fire, the flag stays, and the
        chat grinds forward from its oldest message twenty at a time."""
        chat_id, ids = self.unvouched_chat(client)

        cleared = run_amnesty()

        # THE OUTCOME FIRST, deliberately. A rowcount is bookkeeping; what
        # this repair is for is which end of a two hundred message chat gets
        # paid for, so that is the assertion a mutant has to answer.
        p = plan(chat_id)
        assert p is not None
        assert p["from_id"] == ids[-BATCH], (
            "a flag nothing on disk vouches for still sends this chat back "
            "to its oldest message, one billed call per twenty")
        assert ever_flag(chat_id) == 0
        assert cleared == 1, "the amnesty matched nothing"

    def test_a_chat_that_was_read_survives_the_amnesty(
            self, client) -> None:
        """M2. The hardest case: really read three times, then an edit near
        the start deleted the extraction rows AND the notes, so nothing is
        left to prove it. The label is all that stands between this chat and
        losing its whole history."""
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        really_read(chat_id, ids)
        forget_the_reading_record(chat_id)
        forget_the_notes(chat_id)
        set_state(chat_id, 1, database._EVER_REASON_LEGACY_UNKNOWN)

        run_amnesty()

        assert ever_flag(chat_id) == 1, (
            "a chat that was read three times had its only record of that "
            "cleared by a pass that cannot tell it from a chat nothing "
            "ever touched")
        p = plan(chat_id)
        assert p is not None
        assert p["from_id"] == ids[0]

    def test_the_same_chat_labelled_wide_is_cleared(self, client) -> None:
        """POSITIVE CONTROL for M2, and it is not optional.

        Byte for byte the body above, one word different. Without it the
        claim above is satisfied by an amnesty that does nothing at all - or
        by a build where this column was never added.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        really_read(chat_id, ids)
        forget_the_reading_record(chat_id)
        forget_the_notes(chat_id)
        set_state(chat_id, 1, database._EVER_REASON_BACKFILL_WIDE)

        run_amnesty()

        assert ever_flag(chat_id) == 0
        p = plan(chat_id)
        assert p is not None
        assert p["from_id"] == ids[-BATCH]

    def test_a_surviving_note_holds_the_flag_even_under_the_wide_label(
            self, client) -> None:
        """Belt: the note half of the evidence re-check.

        The predicate does not trust a label written launches ago; it looks
        again. Here the label says the flag came from nowhere and a model
        note says otherwise, and the note wins.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        really_read(chat_id, ids)
        forget_the_reading_record(chat_id)
        set_state(chat_id, 1, database._EVER_REASON_BACKFILL_WIDE)

        assert run_amnesty() == 0
        assert ever_flag(chat_id) == 1

    def test_a_surviving_done_row_holds_the_flag_under_the_wide_label(
            self, client) -> None:
        """Braces: the OTHER half, which nothing tested before.

        Same shape as the test above with the surviving evidence swapped -
        the reading record is intact and the notes are gone. Without this,
        the extraction-side `NOT EXISTS` could be deleted from the predicate
        for free and every gate would stay green.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        really_read(chat_id, ids)
        forget_the_notes(chat_id)
        set_state(chat_id, 1, database._EVER_REASON_BACKFILL_WIDE)

        assert run_amnesty() == 0, (
            "a chat whose reading record is still on disk was cleared by a "
            "pass that only looked at the label and the notes")
        assert ever_flag(chat_id) == 1
        p = plan(chat_id)
        assert p is not None
        assert p["from_id"] != ids[-BATCH]

    def test_clearing_a_flag_takes_its_reason_with_it(self, client) -> None:
        """A dark flag has no reason. A row left saying `backfill_wide` with
        the flag at 0 would be a claim about a chat this build has stopped
        making any claim about, and the next labelling pass would leave it
        there forever - its statements only speak where the reason is NULL.
        """
        chat_id, _ = self.unvouched_chat(client)

        run_amnesty()

        assert ever_flag(chat_id) == 0
        assert reason_of(chat_id) is None, (
            "the flag went out but its reason stayed behind")

    def test_the_shipped_amnesty_matches_nothing_today(self, client) -> None:
        """Said out loud rather than discovered later.

        No build writes `backfill_wide`, so the shipped pass clears zero
        rows on every database this app has ever made. That is the point: the
        mechanism, its predicate and its controls ship reviewed and tested,
        and not one chat we cannot vouch for is touched.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="quiet", from_id=ids[0], to_id=ids[19],
               status="skipped", skip_reason="proxy_gate")
        set_state(chat_id, 1, None)
        database.init_db()
        assert reason_of(chat_id) == database._EVER_REASON_LEGACY_UNKNOWN

        amnesty_key_cleared()
        with get_db() as con:
            database._migrate_ever_flag_amnesty(con)

        assert ever_flag(chat_id) == 1

    def test_a_pardon_nobody_has_spent_is_still_available(
            self, client) -> None:
        """The one-time guard is keyed to the LABEL SET, not to the function.

        A fresh vault runs the shipped pardon on its first launch, where it
        correctly clears nothing. With a single flat key that launch would
        also spend the marker, and a wider pardon turned on later
        would return at the guard on every machine that had ever booted this
        build: shipped, reviewed, tested, and dead on arrival.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="agg", from_id=ids[0], to_id=ids[19],
               status="skipped", skip_reason="proxy_gate")
        set_state(chat_id, 1, database._EVER_REASON_LEGACY_UNKNOWN)

        # Ordinary launches. The shipped pardon spends its own marker.
        database.init_db()
        database.init_db()

        assert amnesty_spent(database._AMNESTY_SAFE_LABELS), (
            "ground: the shipped pardon never ran, so this test is not "
            "measuring what it claims")
        assert not amnesty_spent(database._AMNESTY_AGGRESSIVE_LABELS)
        with get_db() as con:
            database._migrate_ever_flag_amnesty(
                con, database._AMNESTY_AGGRESSIVE_LABELS)
        assert ever_flag(chat_id) == 0, (
            "a pardon that has never been granted was refused as already "
            "spent")

    def test_a_clearing_that_fails_does_not_leave_a_repeatable_pardon(
            self, client, monkeypatch) -> None:
        """The marker is written BEFORE the clearing, and this is why.

        Both statements ride the transaction `init_db` commits and the
        wrapper swallows rather than re-raises. Written last, a failure
        between them left the cleared flags committed with no marker - so the
        pardon fired again on the next launch and the one after that, which
        is the standing licence the guard exists to refuse.
        """
        chat_id, _ = self.unvouched_chat(client)
        amnesty_key_cleared()

        def boom(*a, **kw):
            raise RuntimeError("clearing failed halfway")

        monkeypatch.setattr(database, "_run_ever_flag_amnesty", boom)
        with get_db() as con:
            database._migrate_ever_flag_amnesty(con)

        assert ever_flag(chat_id) == 1, (
            "ground: the clearing this test breaks did not actually break")
        assert amnesty_spent(), (
            "the pardon was not marked spent, so it will fire again on every "
            "launch from here on")

    def test_the_amnesty_runs_once_and_not_on_every_launch(
            self, client) -> None:
        """A reader can light a flag again by reading the chat. A pass that
        ran on every launch would put it back out."""
        chat_id, _ = self.unvouched_chat(client)
        # The fixture's own `init_db` already spent the pardon on an empty
        # vault, which is the honest shape: a real install spends it on its
        # first launch too. Cleared here so this test gets a first pass to
        # measure a second one against.
        amnesty_key_cleared()
        with get_db() as con:
            database._migrate_ever_flag_amnesty(con)
        assert ever_flag(chat_id) == 0, "ground: the first pass did nothing"

        set_state(chat_id, 1, database._EVER_REASON_BACKFILL_WIDE)
        with get_db() as con:
            database._migrate_ever_flag_amnesty(con)

        assert ever_flag(chat_id) == 1, (
            "the one-time pardon ran a second time and would keep clearing "
            "this flag on every launch for the life of the vault")


# ---------------------------------------------------------------------------
# M3 - the unknown set has to be able to shrink
# ---------------------------------------------------------------------------
class TestTheUnknownSetMelts:
    """M3 YAZAR_ETIKETLEMIYOR.

    The old writer guard was `AND notebook_extracted_ever = 0`, which matches
    nothing on a chat a migration already lit. So a chat labelled
    `legacy_unknown` would stay unvouched-for no matter how many real
    extractions landed in it afterwards, and the set of chats nobody can
    account for would freeze at its migration-day size forever.
    """

    def test_a_real_reading_upgrades_a_migrated_label(self, client) -> None:
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        set_state(chat_id, 1, database._EVER_REASON_LEGACY_UNKNOWN)

        settle(chat_id, work_key="upgrade", from_id=ids[0], to_id=ids[19],
               proposals=[fact()])

        assert reason_of(chat_id) == database._EVER_REASON_EXTRACTION

    def test_the_upgrade_is_what_saves_the_chat_from_the_wide_pass(
            self, client) -> None:
        """THE OUTCOME, and the reason the label matters at all.

        The aggressive pass is run here because it is the only variant that
        would touch `legacy_unknown` - and after a genuine reading the label
        is no longer that, so it must not be touched by even the widest pass
        that could be turned on.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        set_state(chat_id, 1, database._EVER_REASON_LEGACY_UNKNOWN)
        settle(chat_id, work_key="upgrade2", from_id=ids[0], to_id=ids[19])
        forget_the_reading_record(chat_id)

        run_amnesty(database._AMNESTY_AGGRESSIVE_LABELS)

        assert ever_flag(chat_id) == 1, (
            "a chat this build watched being read was cleared as one it "
            "knows nothing about")
        p = plan(chat_id)
        assert p is not None
        assert p["from_id"] == ids[0]

    def test_an_attempt_that_read_nothing_does_not_upgrade_anything(
            self, client) -> None:
        """GROUND CONTROL. A writer that labelled every settle would pass
        both tests above while promoting skipped calls to first-hand
        evidence."""
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        set_state(chat_id, 1, database._EVER_REASON_LEGACY_UNKNOWN)

        settle(chat_id, work_key="no-upgrade", from_id=ids[0], to_id=ids[19],
               status="skipped", skip_reason="proxy_gate")

        assert reason_of(chat_id) == database._EVER_REASON_LEGACY_UNKNOWN

    def test_the_writer_stops_writing_once_the_label_is_first_hand(
            self, client) -> None:
        """The old guard's purpose, kept: a settled range must not rewrite
        the same row on every later extraction.

        MEASURED THROUGH THE REAL WRITER, with a trigger counting what it
        does. The first version of this test retyped the production UPDATE
        into its own body and asserted the copy returned rowcount 0 - which
        stays green with the production guard deleted outright, because the
        state it leaves behind satisfies the retyped copy either way. A test
        that cannot go red for the defect it is named after is decoration,
        and retyping the statement broke the rule about constants twice
        over.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        watch_chat_writes()

        settle(chat_id, work_key="first", from_id=ids[0], to_id=ids[19])
        after_first = count_chat_writes()
        for n in range(3):
            settle(chat_id, work_key=f"more-{n}",
                   from_id=ids[20 * (n + 1)], to_id=ids[20 * (n + 2) - 1])

        assert after_first == 1, (
            "ground: the first completed extraction did not write the flag "
            "at all, so this test is measuring nothing")
        assert count_chat_writes() == 1, (
            f"the writer wrote the same row again on every later extraction "
            f"({count_chat_writes()} writes for four settles); the guard that "
            f"stopped that is gone")

    def test_the_writer_does_write_when_the_label_is_a_migration_guess(
            self, client) -> None:
        """POSITIVE CONTROL for the count above. A writer that never wrote
        at all would satisfy it."""
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        set_state(chat_id, 1, database._EVER_REASON_LEGACY_UNKNOWN)
        watch_chat_writes()

        settle(chat_id, work_key="heal-count", from_id=ids[0], to_id=ids[19])

        assert count_chat_writes() == 1
        assert reason_of(chat_id) == database._EVER_REASON_EXTRACTION


# ---------------------------------------------------------------------------
# M4 - the stamp
# ---------------------------------------------------------------------------
class TestAnOlderBuildCannotOpenThis:
    """M4 SURUM_DAMGASI_KALMADI.

    Without the bump, a `v1.1.6` build opens a file carrying this column,
    sees its own version number, and edits it. Its writer
    (`notebook_store.py` at that build) lights the flag and says nothing
    about the reason, and the CHECK does not stop it: the constraint is
    `reason IN (...)`, and NULL against `IN` is NULL, which SQLite counts as
    satisfied. So the row lands lit and unexplained, quietly.

    WHAT THAT COSTS, stated accurately, because the first version of this
    docstring overstated it and the implementation refutes the overstatement.
    Those rows are NOT unlabeled forever: `_label_ever_flag_reasons` runs
    unconditionally on every launch of the newer build and will label them.
    What is lost is the TRUTH of the label. A chat the older build genuinely
    read gets a migration's guess instead of first-hand evidence -
    `backfill_done` while its `done` row survives, and `legacy_unknown` once
    an edit deletes that row - and the second case is permanent, because the
    amnesty that could act on it is once-only and settings-gated. The chat
    joins the set nobody can vouch for, having been read.

    Which is the same hole `_SCHEMA_VERSION` exists to shut, and it has now
    been left open twice before.
    """

    def md5(self, path: str) -> str:
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()

    def test_the_stamp_moved_past_the_build_without_this_column(
            self, client) -> None:
        """ANCHORED TO THE COLUMN, not just to a number.

        `VERSION_BEFORE_THE_REASON_COLUMN` is frozen at 5 on purpose - a
        derived `_SCHEMA_VERSION - 1` would follow every future bump and
        could never go red. But a frozen literal has its own decay: once the
        constant reaches 7 for some unrelated reason, `> 5` stays true and
        this test quietly stops being about the reason column. Asserting the
        column in the same breath is what keeps the two tied together.
        """
        with get_db() as con:
            cols = {r[1] for r in
                    con.execute("PRAGMA table_info(chats)").fetchall()}
        assert "notebook_extracted_ever_reason" in cols, (
            "ground: the column this stamp is about is not in the schema")
        assert database._SCHEMA_VERSION > VERSION_BEFORE_THE_REASON_COLUMN

    def test_an_older_build_is_refused_before_it_writes(
            self, db, monkeypatch) -> None:
        before = self.md5(db)
        monkeypatch.setattr(database, "_SCHEMA_VERSION",
                            VERSION_BEFORE_THE_REASON_COLUMN)

        with pytest.raises(RuntimeError, match="newer version"):
            database.init_db()

        assert self.md5(db) == before, (
            "the guard refused and wrote anyway; its own message promises "
            "nothing was changed")

    def test_the_older_build_never_gets_to_write_an_unlabeled_row(
            self, client, monkeypatch) -> None:
        """THE OUTCOME, not the exception. Measured as the row that must not
        exist afterwards."""
        chat_id = seed(client, 30)
        monkeypatch.setattr(database, "_SCHEMA_VERSION",
                            VERSION_BEFORE_THE_REASON_COLUMN)

        try:
            database.init_db()
            opened = True
        except RuntimeError:
            opened = False

        if opened:
            # What that build's writer does on a completed extraction: it
            # knows the flag and has never heard of the reason.
            with get_db() as con:
                con.execute("UPDATE chats SET notebook_extracted_ever = 1 "
                            "WHERE id = ?", (chat_id,))
        with get_db() as con:
            orphans = con.execute(
                "SELECT COUNT(*) FROM chats WHERE notebook_extracted_ever = 1 "
                "AND notebook_extracted_ever_reason IS NULL").fetchone()[0]
        assert orphans == 0, (
            "a build that predates this column opened the database and lit a "
            "flag it could not explain; nothing will ever be able to explain "
            "it, because the migration that would have runs once")


# ---------------------------------------------------------------------------
# the acceptance measure, in two numbers
# ---------------------------------------------------------------------------
class TestTheAcceptanceMeasure:
    """The roadmap fixed one query - lit flags with no `done` row, target
    zero - and that target cannot be reached honestly: driving it to zero
    means clearing exactly the flags that cannot be decided from the data.

    So it is two numbers. `proven_wrong` must be zero. `unknown` is reported
    and watched, and it shrinks by itself as those chats get read again.
    """

    def test_a_flag_nothing_vouches_for_counts_as_proven_wrong(
            self, client) -> None:
        """GROUND CONTROL: the number is above zero before the repair."""
        chat_id = seed(client, 30)
        set_state(chat_id, 1, database._EVER_REASON_BACKFILL_WIDE)

        with get_db() as con:
            counts = database.notebook_ever_flag_audit(con)

        assert counts["proven_wrong"] == 1

    def test_the_amnesty_drives_proven_wrong_to_zero(self, client) -> None:
        chat_id = seed(client, 30)
        set_state(chat_id, 1, database._EVER_REASON_BACKFILL_WIDE)

        run_amnesty()

        with get_db() as con:
            counts = database.notebook_ever_flag_audit(con)
        assert counts["proven_wrong"] == 0

    def test_the_undecidable_ones_are_reported_and_not_cleared(
            self, client) -> None:
        chat_id = seed(client, 30)
        set_state(chat_id, 1, database._EVER_REASON_LEGACY_UNKNOWN)

        run_amnesty()

        with get_db() as con:
            counts = database.notebook_ever_flag_audit(con)
        assert counts["unknown"] == 1, (
            "the set nobody can vouch for is not being reported, so its size "
            "cannot be watched")
        assert counts["proven_wrong"] == 0
        assert ever_flag(chat_id) == 1

    def test_the_undecided_count_reaches_a_person(self, client, caplog) -> None:
        """A number nothing prints is a comment, not a measure.

        The criterion is that the undecided set is REPORTED and
        watched for smallness. That only means anything if a launch says how
        big it is, so the count is taken on every migration and logged.
        """
        chat_id = seed(client, 30)
        set_state(chat_id, 1, database._EVER_REASON_LEGACY_UNKNOWN)

        with caplog.at_level("INFO", logger="database"):
            database.init_db()

        said = [r for r in caplog.records
                if "cannot decide" in r.getMessage()]
        assert said, (
            "a launch carrying an undecidable flag said nothing about it, so "
            "nobody can watch the number shrink")
        # THE TWO NUMBERS APART, not "is a 1 in the line somewhere". This
        # vault holds one undecidable chat and zero proven-wrong ones, so a
        # line that swapped them would read 1 and 0 the wrong way round and
        # an `in` test could not tell.
        assert said[0].args == (0, 1), (
            f"the launch reported {said[0].args}, so the number a person "
            f"reads is not the number the audit measured")

    def test_a_healthy_vault_says_nothing(self, client, caplog) -> None:
        """GROUND CONTROL. A line printed on every launch is noise, and noise
        is how a real one gets ignored."""
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="quiet-log", from_id=ids[0], to_id=ids[19])

        with caplog.at_level("INFO", logger="database"):
            database.init_db()

        assert not [r for r in caplog.records
                    if "cannot decide" in r.getMessage()]

    def test_a_chat_with_evidence_is_in_neither_number(self, client) -> None:
        """POSITIVE CONTROL. A counter that counted every lit flag would
        satisfy the two tests above and would report a healthy vault as
        broken."""
        chat_id = seed(client, 30)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="healthy", from_id=ids[0], to_id=ids[19])

        with get_db() as con:
            counts = database.notebook_ever_flag_audit(con)

        assert counts == {"proven_wrong": 0, "unknown": 0}
