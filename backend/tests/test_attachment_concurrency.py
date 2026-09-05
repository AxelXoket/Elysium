"""The three attachment races that were guarded by a comment and nothing else.

Each of the three code paths below carries a comment saying what it protects
against, and TWO of the three had nothing that could contradict the comment.
Measured against the four test files that touch these functions: delete
`link_attachments`'s `AND message_id IS NULL` and all 105 of them still pass;
delete the purge's upfront `BEGIN IMMEDIATE` and all 105 still pass. With
this file present each of those is one red. A protection nothing can turn red
is a protection the next reader is free to call redundant.

The third is not like the other two, and it is worth writing down plainly
because the record that asked for this file says otherwise. Deleting
`save_upload`'s `INSERT OR IGNORE` already fails five tests in
test_attachments.py - `test_upload_dedupes_identical_files`,
`test_upload_dedup_stores_single_blob`, `test_shared_blob_survives_partial_delete`,
`test_unstage_shared_blob_survives_other_staged_row` and
`test_deleting_a_character_takes_its_pictures_and_leaves_the_others`. That
uniqueness protection was never unpinned. Only its concurrent shape was, and
the class below is honest about how little that adds.

The regression surface is also smaller than a name search suggests. Grepping
the test tree for these three names returns fourteen files, but ten of them
match `refs.save_upload`, an unrelated function in the voice-reference
module. Only four test files touch `attachments_service`'s versions at all,
and not one of them puts two writers in flight.

WHY THREADS AND NOT A NARRATIVE

A sequential test can describe a race but cannot lose one. Two of the three
invariants here are only visible when two writers are genuinely in flight:
which of two sends owns a staged image, and whether a purge that has already
chosen its victims can be talked out of deleting one. So these drive real
threads, each with its own connection, which is also how the application
reaches these functions - every request runs on a worker thread of its own.

WHY THERE IS NO SLEEP IN THIS FILE

Gate tests do not get to be flaky, and a race test written with sleeps is a
race test that fails on a loaded machine. Two devices replace the sleeps.

The first is a `threading.Barrier`: both threads are held until both have
finished their reads, so they enter the write window together. Nothing then
depends on WHICH of them wins - the assertions are symmetric, and say only
that exactly one did.

The second is a hook on the one call that sits between the purge's SELECT and
its DELETE. Patching it opens the window at exactly the statement boundary the
comment is about, rather than hoping a sleep lands there.

WHAT THIS FILE DOES NOT PIN, MEASURED

Two of the three guards are held here by a red that only concurrency can
produce: delete `AND message_id IS NULL` and two sends are both told they own
the same image; delete the purge's `BEGIN IMMEDIATE` and the purge removes a
row a send has already been handed.

The third is not the same kind of thing, and saying so is the point. Deleting
`save_upload`'s own `BEGIN IMMEDIATE` while leaving `INSERT OR IGNORE` in
place keeps every test below green - measured, not assumed. It has to: the
first statement inside that transaction is already a write, so an implicit
deferred transaction takes the writer lock at precisely the same instant. The
line records intent and buys no behaviour, and no behavioural test can pin it.

What actually protects the duplicate upload is the `OR IGNORE`, and removing
it fails two SEQUENTIAL identical uploads as well - so that red is real, is
already covered by test_attachments.py, and is not evidence about concurrency
either way. What the class below adds over those five is one narrow thing: it
is the only one that puts the two uploads in flight together, so it is the
only one that would notice a future dedup rewritten as SELECT-then-INSERT.

All of this is written down rather than left for the next reader to
rediscover, because a file called _concurrency claiming three concurrency
guards when it holds two is exactly the overstatement these tests exist to
stop.
"""
from __future__ import annotations

import io
import threading

import pytest
from PIL import Image
# The driver the application actually runs on, imported the way database.py
# imports it. Its OperationalError is NOT stdlib sqlite3's, and catching the
# wrong one turns "the lock was held" into an exception escaping a thread.
from sqlcipher3 import dbapi2 as sqlite3

import attachments_service
from attachments_service import (
    link_attachments,
    purge_stale_staged,
    save_upload,
)
from database import get_db

#: Short enough that the blocked side gives up quickly, long enough that it is
#: a lock decision rather than a scheduling accident. Only the thread that is
#: EXPECTED to be held off ever waits this long.
BLOCKED_TIMEOUT_MS = 250


def png_bytes(colour: tuple[int, int, int], size: int = 8) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def seed_message(text: str = "carrier") -> int:
    """A real message row for a staged image to be linked to.

    Written in SQL rather than through the API because the attachments layer
    is what is under test here; a provider stub and an HTTP client would be
    two more things that could explain a red.
    """
    with get_db() as con:
        character_id = con.execute(
            "INSERT INTO characters (name) VALUES ('concurrency')"
        ).lastrowid
        chat_id = con.execute(
            "INSERT INTO chats (character_id) VALUES (?)", (character_id,)
        ).lastrowid
        return con.execute(
            "INSERT INTO messages (chat_id, role, content) "
            "VALUES (?, 'user', ?)",
            (chat_id, text),
        ).lastrowid


def staged_owner(attachment_id: int) -> int | None:
    """The message_id column of one attachment row, or None when it is gone."""
    with get_db() as con:
        row = con.execute(
            "SELECT message_id FROM attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
    return row["message_id"] if row else None


def attachment_exists(attachment_id: int) -> bool:
    with get_db() as con:
        return con.execute(
            "SELECT 1 FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone() is not None


def blob_count(sha: str) -> int:
    with get_db() as con:
        return con.execute(
            "SELECT COUNT(*) AS c FROM attachment_blobs WHERE sha256 = ?",
            (sha,),
        ).fetchone()["c"]


class TestOnlyOneMessageCanClaimAStagedImage:
    """`link_attachments`, and the `AND message_id IS NULL` in its UPDATE.

    Two sends can validate the same staged id before either of them writes -
    the validation runs on a connection that holds no lock, which is exactly
    why the guard is in the UPDATE and not in a prior SELECT. Without it the
    second send silently steals the image, and BOTH replies tell their caller
    they have it. One of those two replies is then a lie that renders as a
    picture on the wrong message.
    """

    def test_a_single_send_links_its_own_image(self, db) -> None:
        """Ground control: nothing about the guard breaks the ordinary path."""
        message_id = seed_message()
        staged = save_upload(png_bytes((3, 5, 7)), "image/png")

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            linked = link_attachments(con, [staged["id"]], message_id)

        assert [row["id"] for row in linked] == [staged["id"]]
        assert staged_owner(staged["id"]) == message_id

    def test_two_sends_racing_for_one_image_leave_exactly_one_owner(
        self, db
    ) -> None:
        first_message = seed_message("first")
        second_message = seed_message("second")
        staged = save_upload(png_bytes((11, 13, 17)), "image/png")
        attachment_id = staged["id"]

        barrier = threading.Barrier(2)
        observed: dict[str, bool] = {}
        results: dict[str, object] = {}

        def send(name: str, message_id: int) -> None:
            # The pre-write read, on its own connection and under no lock:
            # the shape the production validate step has. Both threads do it
            # BEFORE the barrier, so both are guaranteed to have seen the row
            # staged - otherwise a fast first thread would turn this into two
            # sequential sends wearing a race's clothes.
            observed[name] = staged_owner(attachment_id) is None
            barrier.wait(timeout=10)
            try:
                with get_db() as con:
                    con.execute("BEGIN IMMEDIATE")
                    results[name] = link_attachments(
                        con, [attachment_id], message_id)
            except Exception as exc:  # pragma: no cover - reported below
                results[name] = exc

        threads = [
            threading.Thread(target=send, args=("first", first_message)),
            threading.Thread(target=send, args=("second", second_message)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "a linking thread never finished"

        # Positive control. Two claims that never overlapped would satisfy
        # every assertion below for the boring reason, so the overlap itself
        # is asserted: both threads saw an unclaimed image before either wrote.
        assert observed == {"first": True, "second": True}
        for name, result in results.items():
            assert not isinstance(result, Exception), f"{name}: {result!r}"

        claimed = {name: [row["id"] for row in rows]
                   for name, rows in results.items()}
        winners = [name for name, ids in claimed.items()
                   if attachment_id in ids]
        assert len(winners) == 1, (
            f"both sends were told they own the image: {claimed}. The staged "
            f"row was claimed twice, so one of these two messages is showing "
            f"a picture that belongs to the other.")

        # And the winner's answer matches the database, rather than merely
        # being the only non-empty one.
        expected = {"first": first_message, "second": second_message}
        assert staged_owner(attachment_id) == expected[winners[0]]


class TestTwoIdenticalUploadsAtOnce:
    """`save_upload`, and the `INSERT OR IGNORE` on the content-addressed blob.

    Blobs are keyed by sha256, so two uploads of the same picture are two
    attachment rows over one blob. Sending the same image from two places at
    once is the ordinary way to reach that: a retry, a double click, the same
    file dropped on two chats. If the second insert of an identical blob is
    an error rather than a no-op, one of the two uploads fails, and it fails
    at the point where the user has already waited for the encode.

    Scope, honestly: the guard this pins is the `OR IGNORE`, not the
    `BEGIN IMMEDIATE` above it, and five tests in test_attachments.py pin the
    `OR IGNORE` already. What is only here is the concurrent shape. See the
    module docstring for the measurements behind both sentences.
    """

    def test_one_upload_stages_one_row_and_one_blob(self, db) -> None:
        """Ground control for the pair below."""
        payload = png_bytes((19, 23, 29))
        staged = save_upload(payload, "image/png")

        with get_db() as con:
            sha = con.execute(
                "SELECT sha256 FROM attachments WHERE id = ?", (staged["id"],)
            ).fetchone()["sha256"]
        assert blob_count(sha) == 1
        assert attachments_service.get_blob(staged["id"])[1]

    def test_two_concurrent_identical_uploads_both_survive_one_blob(
        self, db
    ) -> None:
        payload = png_bytes((31, 37, 41))
        barrier = threading.Barrier(2)
        results: dict[str, object] = {}

        def upload(name: str) -> None:
            barrier.wait(timeout=10)
            try:
                results[name] = save_upload(payload, "image/png")
            except Exception as exc:
                results[name] = exc

        threads = [threading.Thread(target=upload, args=(name,))
                   for name in ("left", "right")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "an uploading thread never finished"

        for name, result in results.items():
            assert not isinstance(result, Exception), (
                f"{name} upload failed while an identical one was in flight: "
                f"{result!r}")

        left, right = results["left"], results["right"]
        assert left["id"] != right["id"], "the two uploads collapsed into one row"

        with get_db() as con:
            shas = {row["sha256"] for row in con.execute(
                "SELECT sha256 FROM attachments WHERE id IN (?, ?)",
                (left["id"], right["id"]),
            ).fetchall()}
        assert len(shas) == 1, "identical bytes produced two content addresses"
        assert blob_count(next(iter(shas))) == 1

        # Both rows resolve to the shared blob, and to the same bytes. A dedup
        # that stored the row but lost the join would leave two ids that serve
        # nothing, which is the failure a row count alone cannot see.
        served = [attachments_service.get_blob(row["id"])
                  for row in (left, right)]
        assert all(item is not None for item in served)
        assert served[0][1] == served[1][1]


class TestThePurgeCannotTakeAnImageALinkAlreadyClaimed:
    """`purge_stale_staged`, and the `BEGIN IMMEDIATE` above its SELECT.

    The purge picks its victims with a SELECT and removes them with a DELETE.
    Between those two statements the rows are only staged in the purge's
    opinion. Without the upfront write lock that read runs in autocommit, a
    send can claim one of the chosen rows in the gap, and the DELETE then
    removes an image that now belongs to a message - the caller having already
    been told the link succeeded.

    The window is opened at exactly that statement boundary by hooking the one
    call between them, so nothing here depends on timing.
    """

    def _stage_a_stale_image(self) -> int:
        staged = save_upload(png_bytes((43, 47, 53)), "image/png")
        with get_db() as con:
            con.execute(
                "UPDATE attachments SET created_at = datetime('now', '-48 hours') "
                "WHERE id = ?",
                (staged["id"],),
            )
        return staged["id"]

    def test_the_purge_removes_a_stale_staged_image(self, db) -> None:
        """Ground control: with nobody racing it, the purge still sweeps."""
        attachment_id = self._stage_a_stale_image()
        assert purge_stale_staged(hours=24) == 1
        assert not attachment_exists(attachment_id)

    def test_a_fresh_staged_image_is_left_alone(self, db) -> None:
        """The other half of the ground control: it sweeps the right rows.

        Without this, a purge that deleted everything would satisfy the test
        above and the race test below equally well.
        """
        staged = save_upload(png_bytes((59, 61, 67)), "image/png")
        assert purge_stale_staged(hours=24) == 0
        assert attachment_exists(staged["id"])

    def test_a_send_racing_the_purge_never_loses_a_claimed_image(
        self, db, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        message_id = seed_message()
        attachment_id = self._stage_a_stale_image()

        link = {"ran": False, "blocked": False, "claimed": []}

        def claim() -> None:
            link["ran"] = True
            try:
                # The short timeout is what makes this bounded: if the purge
                # is holding the writer lock, this side gives up rather than
                # queueing behind a transaction that is waiting on it.
                with get_db(busy_timeout_ms=BLOCKED_TIMEOUT_MS) as con:
                    rows = link_attachments(con, [attachment_id], message_id)
                link["claimed"] = [row["id"] for row in rows]
            except sqlite3.OperationalError as exc:
                link["blocked"] = "locked" in str(exc).lower()
                link["error"] = str(exc)

        real_iter_chunks = attachments_service.iter_chunks
        window = {"opened": 0, "targets": []}

        def iter_chunks_with_the_window_open(seq, *args, **kwargs):
            # `_delete_orphan_blobs` reaches for the same helper afterwards,
            # so only the first call - the DELETE loop's - is the boundary
            # this test means.
            if window["opened"] == 0:
                window["opened"] = 1
                window["targets"] = list(seq)
                thread = threading.Thread(target=claim)
                thread.start()
                thread.join(timeout=30)
                assert not thread.is_alive(), "the claiming thread hung"
            return real_iter_chunks(seq, *args, **kwargs)

        monkeypatch.setattr(attachments_service, "iter_chunks",
                            iter_chunks_with_the_window_open)
        removed = purge_stale_staged(hours=24)

        # Positive controls: the window really opened, on our row, and the
        # send really ran inside it. All three assertions below are about what
        # happened in that window, and every one of them is satisfiable by a
        # window that never opened.
        assert window["opened"] == 1, "the purge never reached its DELETE"
        assert window["targets"] == [attachment_id]
        assert link["ran"] is True
        assert removed == 1

        # The consequence first, because it is the invariant and the lock is
        # only the mechanism: nothing the send was TOLD it owns may be missing
        # once the purge has finished.
        with get_db() as con:
            survivors = {row["id"] for row in con.execute(
                "SELECT id FROM attachments WHERE message_id = ?",
                (message_id,),
            ).fetchall()}
        assert set(link["claimed"]) <= survivors, (
            f"the purge deleted an image a concurrent send had already been "
            f"told it owns: the send was handed {link['claimed']} and the "
            f"message ended up with {sorted(survivors)}")

        # Then the mechanism, which is what carries this test on the days the
        # assertion above is vacuous. While the purge sits between its SELECT
        # and its DELETE it holds the writer lock, so the competing claim
        # cannot get in at all - and `claimed` is empty for a REASON rather
        # than by luck.
        assert link["blocked"] is True, (
            "the send was NOT held off by the purge's write lock, so the "
            "purge's SELECT and DELETE are not one atomic step and the rows "
            "it chose can change underneath it: "
            f"claimed={link['claimed']}")
