"""The version stamp and the shape it claims to describe, tied together.

`_SCHEMA_VERSION` is the whole of the downgrade guard. `_migrate` refuses a
database stamped higher than the running build understands, and the argument
for refusing - written into `database.py` itself - is that an older build
"will happily write rows that the newer schema's constraints would have
refused, and nothing notices".

That argument only holds if the number moves whenever the shape does. It did
not. `notebook_entries.supersedes_id`, `notebook_spend.cost_unknown` and the
`notebook_spend_calls` ledger all landed while the constant sat at 4, so for
those three objects the guard was disarmed: a build that predated them would
have opened the file, seen its own version number, and edited it.

Nobody catches that by remembering. So the reminder is mechanical: this file
records one fingerprint per schema version, computed from the database the
build actually creates. Add an object without bumping the constant and the
recorded fingerprint for that version no longer matches - the test fails and
says what to do. The mistake becomes unreachable rather than unlikely.

Deliberately NOT a source-text check: the fingerprint is taken from a live
database's `sqlite_master`, so it sees the CREATE-only schema, every guarded
`ALTER`, and every index the migration adds, exactly as they end up on a
user's disk.

One exception, under decision KARAR-V2-10: a new table created with CREATE
TABLE IF NOT EXISTS (nothing altered, nothing an older build would write into)
may re-record the CURRENT version's row instead of bumping the version.

A second, named exception, under decision 59 a (27 Sep 2026): range
reading's whole schema lands in ONE record under the same stamp, and the
version 6 row is re-recorded for it exactly ONCE. The value it replaced is
kept below as `FINGERPRINT_6_BEFORE_RANGE_READING` rather than in a comment,
because the migration test imports it to prove its fixture is that shape.
Any further change under 6 is a bump or a new, separately decided exception
(63 a: Baslik 7.1 and 7.2 each record their own), never a quiet re-record.
"""
from __future__ import annotations

import hashlib
import re

import pytest

import database


#: version -> fingerprint of the database that version describes.
#
# TO ADD A ROW: bump `database._SCHEMA_VERSION`, run this test once, and paste
# the fingerprint it prints. Do NOT edit an existing row - a fingerprint that
# changes under a version number that does not is the exact defect this file
# exists to catch, and rewriting the expectation to match is how it would be
# missed a second time.
FINGERPRINTS: dict[int, str] = {
    5: "cfe4b3451cf67feb77d541d70c720a39ee1b7c3f2b7568bcd96fdf792565ef78",
    # 6 adds `chats.notebook_extracted_ever_reason`. Recorded from what this
    # gate printed when it went red on the bump, not computed by hand - the
    # whole value of the row is that it came from the database the build
    # actually creates. The row for 5 is untouched.
    # 6 also carries the backfill job table and the two `notebook_entries`
    # columns beside it (`job_id`, `unmarked_in_source`), and this row is
    # the ONE deliberate exception to the rule three lines up. I re-recorded
    # it from the value this gate printed when it went red, not by hand,
    # and the version stayed at 6 on purpose: KARAR-V2-10 puts the three
    # additions in one migration behind PRAGMA table_info guards and
    # refuses a bump, and S-5 says the same - a guarded ADD COLUMN is
    # gated by the column's absence, not by the stamp. What that costs is
    # named: version 6 now describes two shapes on shipped builds, the way
    # version 3 once did, and a 6-build that predates the additions opens
    # this file and leaves the job table and both nullable columns alone.
    # The row for 5 is untouched, and the earlier value of this row was
    # 9aa437cf4bc57a9b75e89f8aaeccfb9585c413ae8a741f7e6b940a77ab7893b7.
    # Rebased onto prompt-layer (27 Sep 2026): 6 now carries BOTH additions,
    # the two prompt module tables that branch added (CREATE IF NOT EXISTS,
    # no bump, recorded there as 5ed3469c28534b54d7c7a87b409014ed6b2b4d4b2668dbbcd27c7baaf65c4605)
    # and the backfill job table with its two columns above (recorded alone
    # as 24cdb902209f5c588c261fed602f668981bdb2798f0ff75fc53478bfed5f0cb7).
    # The combined value is the one this gate printed on the merged schema.
    # RE-RECORDED ONCE MORE, by decision 59 a: range reading's single schema
    # record (`database._migrate_range_reading`: four `notebook_entries`
    # columns, `notebook_extractions.reader_model`, two tables, two indexes)
    # stays under 6. Pasted from what this gate printed when it went red on
    # that record, not computed by hand. The value it replaced is
    # FINGERPRINT_6_BEFORE_RANGE_READING below. The record's two tables
    # carry ON DELETE CASCADE on `chat_id` and a hex-only digest CHECK; both
    # are part of the same one record, so this is still the one re-record
    # against the previous commit that 59 a allows, not a second one.
    6: "0a460f16472c2b69b43b66ec725d17f5ff4407c4777aee085a705169e48fab47",
}

#: Version 6 as it stood before the 59 a record, i.e. the row above's
#: previous value. Not a live expectation: it is the identity of the shape
#: `tests/fixtures/schema6_before_range_reading.sql` must reproduce, so the
#: migration test can prove it starts from the real previous schema.
FINGERPRINT_6_BEFORE_RANGE_READING = (
    "de6cdc00c46e97550c1124532deda3813a4072f0f6f26a1d0a56027fb4c10be1")


def _fingerprint(con) -> str:
    """Every object the schema creates, whitespace-normalised and hashed.

    `sqlite_master.sql` holds the CREATE text as SQLite stored it, which for a
    table includes every column - so a guarded `ADD COLUMN` shows up here as
    surely as a new table does. Autoindexes carry a NULL `sql` and a generated
    name; they are implied by their table's UNIQUE clause, which is already in
    the hash, so they are skipped rather than baked in.
    """
    rows = con.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL").fetchall()
    parts = sorted(
        "|".join((r[0], r[1], r[2], re.sub(r"\s+", " ", r[3]).strip()))
        for r in rows)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


class TestTheStampDescribesTheShape:
    def test_the_shape_matches_the_version_it_is_stamped_with(self, db) -> None:
        with database.get_db() as con:
            stamped = con.execute("PRAGMA user_version").fetchone()[0]
            actual = _fingerprint(con)

        assert stamped == database._SCHEMA_VERSION, (
            "ground: a freshly built database carries this build's stamp")

        expected = FINGERPRINTS.get(stamped)
        assert expected is not None, (
            f"schema version {stamped} has no recorded fingerprint. If you "
            f"just bumped the constant, add {stamped}: {actual!r} to "
            f"FINGERPRINTS.")
        assert actual == expected, (
            f"the schema changed but _SCHEMA_VERSION is still {stamped}. An "
            f"older build would open this database and edit it, which is what "
            f"the downgrade guard exists to prevent. Bump the constant and "
            f"record {actual!r} against the new number - do not overwrite the "
            f"row for {stamped}.")

    def test_the_three_objects_that_went_unstamped_are_covered_now(
            self, db) -> None:
        """The specific defect, named.

        Positive control for the test above: these three are the objects
        that landed under version 4. If the fingerprint gate were vacuous -
        hashing nothing, or hashing something that does not include columns
        - this would still fail.

        `chats.notebook_auto_accept_override` is asserted alongside them and
        deliberately NOT counted among them: it landed in the same commit as
        `_SCHEMA_VERSION = 3` and was stamped correctly from birth. It was
        named as a fourth in the first version of this file, which is the
        same class of mistake the file exists to catch, one layer up.
        """
        with database.get_db() as con:
            entry_cols = {r[1] for r in con.execute(
                "PRAGMA table_info(notebook_entries)").fetchall()}
            spend_cols = {r[1] for r in con.execute(
                "PRAGMA table_info(notebook_spend)").fetchall()}
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            chat_cols = {r[1] for r in con.execute(
                "PRAGMA table_info(chats)").fetchall()}
            stamped = con.execute("PRAGMA user_version").fetchone()[0]

        assert "supersedes_id" in entry_cols, (
            "notebook_entries.supersedes_id is gone; it is one of the three "
            "objects the version bump was for")
        assert "cost_unknown" in spend_cols, (
            "notebook_spend.cost_unknown is gone; without it an unpriced "
            "call reads as a free one")
        assert "notebook_spend_calls" in tables, (
            "the per-call ledger is gone; it is what stops one physical "
            "call being counted twice")
        # Stamped from birth under 3; here so a fingerprint that stopped
        # seeing columns fails loudly, not as one of the three.
        assert "notebook_auto_accept_override" in chat_cols, (
            "chats.notebook_auto_accept_override is gone - not one of the "
            "three, but its absence would mean the fingerprint had stopped "
            "seeing columns at all")
        assert stamped > 4, (
            "all three exist, so a build that understands only 4 must be "
            "refused - and it is only refused if the stamp is above 4")
        # And the three that landed under 6 WITHOUT a bump (KARAR-V2-10):
        # positive controls for the re-recorded row, so a fingerprint that
        # matched by accident with one of them missing would still fail.
        assert "notebook_backfill_jobs" in tables, (
            "the backfill job table is gone; a run cannot be undone without it")
        assert "job_id" in entry_cols, (
            "notebook_entries.job_id is gone; undo would have nothing to "
            "select by")
        assert "unmarked_in_source" in entry_cols, (
            "notebook_entries.unmarked_in_source is gone; it lands with the "
            "job table in one migration")
        # And the 59 a record, same reason: the row was re-recorded for
        # exactly these, so each one's absence must fail by name.
        for name, _decl in database._RANGE_READING_ENTRY_COLUMNS:
            assert name in entry_cols, f"notebook_entries.{name} is gone"
        with database.get_db() as con:
            extraction_cols = {r[1] for r in con.execute(
                "PRAGMA table_info(notebook_extractions)").fetchall()}
            indexes = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()}
        for name, _decl in database._RANGE_READING_EXTRACTION_COLUMNS:
            assert name in extraction_cols, (
                f"notebook_extractions.{name} is gone")
        for table in database._RANGE_READING_TABLES:
            assert table in tables, f"{table} is gone"
        for index in database._RANGE_READING_INDEXES:
            assert index in indexes, f"{index} is gone"

    def test_the_fingerprint_notices_a_new_column(self, db) -> None:
        """The gate can go red.

        A hash nobody has ever seen fail is decoration. This adds a column
        the way `_migrate` does and measures that the fingerprint moves - so
        a future unstamped `ADD COLUMN` cannot slip past it.

        SAID PLAINLY: this exercises `_fingerprint`, which lives in this
        file, so it buys no production coverage of its own. It is the
        harness's self-check, and it is here because the two tests above
        both rest on the claim that a schema change moves the hash. Counting
        it as a third test of the schema would be counting the ruler as one
        of the measurements.
        """
        with database.get_db() as con:
            before = _fingerprint(con)
            con.execute("ALTER TABLE notebook_entries "
                        "ADD COLUMN nothing_at_all INTEGER")
            after = _fingerprint(con)
        assert before != after, (
            "a column was added and the fingerprint did not move; the gate "
            "would not have caught the defect it was written for")
