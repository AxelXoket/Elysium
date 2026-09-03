"""H-04 - the reason a run failed reaches a screen.

`error_type` has been written on every failed extraction since the column
existed, and every reader of it asks one fixed question and answers in a
single number: the two counters in `extraction_stats` ("was it abandoned",
"did it cost money") and the cursor queries that step over an abandoned row.
None of them can say what the value WAS. So "it failed three times" and "your
API key was refused three times" arrived on the panel as the same sentence,
and only one of those two is something a person can do anything about.

The breakdown here is the same shape `skip_reasons` has given refusals from
the start. What it is NOT is a replacement for `abandoned`: that count is
nested inside `failed` and the two may never be added, which is a property of
the counter rather than of a census, so it keeps its own query.
"""
from __future__ import annotations

import notebook_extract
import notebook_store as notebook
import notebook_worker
import openrouter
from database import get_db

from tests.test_notebook_worker import seed


def _settle(chat_id: int, key: str, error_type: str | None,
            status: str = "failed") -> None:
    """One settled extraction row, written through the real store path.

    `status` is a parameter rather than always `failed` because the NULL
    filter below needs a row that did NOT fail, and inventing one by hand
    would be measuring a fixture instead of the writer.
    """
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        notebook.commit_extraction(
            con, work_key=key, chat_id=chat_id, from_id=1, to_id=4,
            proposals=[], status=status, error_type=error_type)


def _skip(chat_id: int, key: str, reason: str) -> None:
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        notebook.commit_extraction(
            con, work_key=key, chat_id=chat_id, from_id=1, to_id=4,
            proposals=[], status="skipped", skip_reason=reason)


def _stats(chat_id: int) -> dict:
    with get_db() as con:
        return notebook.extraction_stats(con, chat_id)


#: A refusal token taken from the store's own frozen vocabulary rather than
#: retyped: the positive control below is only a control if the reason it
#: uses is one this writer will actually accept.
A_REAL_SKIP_REASON = sorted(notebook.SKIP_REASONS)[0]


class TestWhyItFailedIsCounted:
    def test_the_classes_are_counted_apart(self, client) -> None:
        """THE CLAIM. Two timeouts and one failed write are not "three
        failures"; they are two different things that call for two different
        responses from the reader."""
        chat_id = seed(client, count=6)
        _settle(chat_id, "e1", "ReadTimeout")
        _settle(chat_id, "e2", "ReadTimeout")
        _settle(chat_id, "e3", "write_RuntimeError")

        stats = _stats(chat_id)

        assert stats["error_types"] == {"ReadTimeout": 2,
                                        "write_RuntimeError": 1}
        # Free, and it covers the one branch of `paid_and_lost` that the
        # abandoned fixtures below cannot reach: the `write_` prefix match.
        assert stats["paid_and_lost"] == 1

    def test_the_existing_count_is_untouched(self, client) -> None:
        """GROUND. The same fixture still totals three failures. A breakdown
        that came at the cost of the number beside it would be a trade, not a
        fix."""
        chat_id = seed(client, count=6)
        _settle(chat_id, "e1", "ReadTimeout")
        _settle(chat_id, "e2", "ReadTimeout")
        _settle(chat_id, "e3", "write_RuntimeError")

        assert _stats(chat_id)["failed"] == 3

    def test_the_census_accounts_for_every_failure(self, client) -> None:
        """The invariant the panel's sentence rests on.

        The card renders each row as "N of <failed> failures", so the census
        adding up to something other than `failed` would put arithmetic on
        screen that does not close. Four rows, four different shapes, and the
        sum has to be the same number the counter reports.
        """
        chat_id = seed(client, count=6)
        _settle(chat_id, "x1", notebook.ABANDONED_IN_FLIGHT)
        _settle(chat_id, "x2", "write_RuntimeError")
        _settle(chat_id, "x3", "prompt_KeyError")
        _settle(chat_id, "x4", "truncated")

        stats = _stats(chat_id)

        assert sum(stats["error_types"].values()) == stats["failed"] == 4

    def test_the_breakdown_that_already_worked_still_does(self, client) -> None:
        """POSITIVE CONTROL, because the claim above is an ABSENCE claim.

        `skip_reasons` is the breakdown this store has always produced. A
        failed row is written into the SAME fixture, so the control rules out
        the thing it is here to rule out: a `commit_extraction` that wrote
        nothing at all would take `skip_reasons` down with it, and a reader
        that had simply stopped answering would empty both.
        """
        chat_id = seed(client, count=6)
        _skip(chat_id, "s1", A_REAL_SKIP_REASON)
        _settle(chat_id, "s2", "ReadTimeout")

        stats = _stats(chat_id)

        assert stats["skip_reasons"] == {A_REAL_SKIP_REASON: 1}
        assert stats["skipped"] == 1
        assert stats["failed"] == 1

    def test_a_row_that_did_not_fail_adds_no_nameless_reason(
            self, client) -> None:
        """The NULL filter, and it is the same `if r[0]` guard `skip_reasons`
        uses. Every row that did not fail carries NULL in this column, so
        without it the commonest key in the census would be no key at all."""
        chat_id = seed(client, count=6)
        _settle(chat_id, "ok", None, status="done")
        _settle(chat_id, "bad", "ReadTimeout")

        types = _stats(chat_id)["error_types"]

        assert types == {"ReadTimeout": 1}
        assert None not in types

    def test_the_abandoned_count_is_not_replaced_by_the_census(
            self, client) -> None:
        """The counter that must survive this change.

        `abandoned` is counted INSIDE `failed`, so it is the one number on
        this card that may not be added to its neighbours. It keeps its own
        query and its own answer; the census reports the same rows under
        their own name, which is a different sentence about the same event.
        """
        chat_id = seed(client, count=6)
        _settle(chat_id, "a1", notebook.ABANDONED_IN_FLIGHT)
        _settle(chat_id, "t1", "ReadTimeout")

        stats = _stats(chat_id)

        assert stats["abandoned"] == 1
        assert stats["paid_and_lost"] == 1
        assert stats["failed"] == 2
        assert stats["error_types"] == {notebook.ABANDONED_IN_FLIGHT: 1,
                                        "ReadTimeout": 1}

    def test_the_census_is_scoped_to_the_chat_it_was_asked_about(
            self, client) -> None:
        """The `where` clause, which the new query has to carry like the two
        beside it. A breakdown that quietly reported every chat would put
        another conversation's trouble on this one's panel."""
        mine = seed(client, count=6)
        theirs = seed(client, count=6)
        _settle(mine, "m1", "ReadTimeout")
        _settle(theirs, "t1", "write_RuntimeError")

        assert _stats(mine)["error_types"] == {"ReadTimeout": 1}
        assert _stats(theirs)["error_types"] == {"write_RuntimeError": 1}

    def test_asked_about_no_chat_it_answers_for_all_of_them(
            self, client) -> None:
        """GROUND for the line above: the route calls this with no chat id,
        so a `where` that was always on would empty the panel."""
        mine = seed(client, count=6)
        theirs = seed(client, count=6)
        _settle(mine, "m1", "ReadTimeout")
        _settle(theirs, "t1", "ReadTimeout")

        with get_db() as con:
            everywhere = notebook.extraction_stats(con)

        assert everywhere["error_types"]["ReadTimeout"] == 2


class TestTheShapeTheWriterActuallyProduces:
    """Hand-typed fixtures measure the reader and nothing else.

    Every value above was invented by this file, so a change to how the
    worker BUILDS the token would leave all of it green while the census
    filled with a shape no screen has words for. These two ask the real
    producer what it writes and count that.
    """

    def test_a_provider_failure_lands_under_the_name_its_writer_gives_it(
            self, client) -> None:
        chat_id = seed(client, count=6)
        # The producer, called rather than imitated. `_failure_type` puts
        # the app's own reason code after the class name, and that colon is
        # the shape the panel branches on to borrow an existing sentence.
        written = notebook_worker._failure_type(
            openrouter.OpenRouterError("openrouter_timeout"))
        _settle(chat_id, "p1", written)

        assert _stats(chat_id)["error_types"] == {written: 1}
        assert ":" in written, "ground: this really is the two-part shape"

    def test_a_parser_refusal_lands_as_a_bare_token(self, client) -> None:
        """The third producer, and the one whose SHAPE is easiest to get
        wrong when reading the file rather than running it.

        `parse_reply` is not called inside the `try` around the provider
        call, so its `ExtractionFailed` never reaches `_failure_type`: the
        settle path records `str(exc)`, which is the bare token with no
        class name and no colon in front of it. The panel has a sentence for
        each of those eight tokens and branches on the colon to decide
        whether to borrow a reason code's English instead. If this ever
        started going through `_failure_type` the key would become
        `ExtractionFailed:<token>`, every one of those sentences would stop
        matching, and a snake_case token would reach the reader - which is
        the one thing the panel's table exists to prevent.
        """
        chat_id = seed(client, count=6)
        # The real raise, from the real parser, on a reply shaped the way a
        # provider really can answer.
        raised: notebook_extract.ExtractionFailed | None = None
        try:
            notebook_extract.parse_reply({"choices": []}, "chunk", [])
        except notebook_extract.ExtractionFailed as exc:
            raised = exc
        assert raised is not None, (
            "ground: an empty reply must be refused, or this test is "
            "measuring nothing")
        written = str(raised)

        _settle(chat_id, "b1", written)

        assert ":" not in written, (
            "the panel reads a colon as 'a reason code follows'")
        assert _stats(chat_id)["error_types"] == {written: 1}
        # And the direction, so this is a claim rather than a coincidence:
        # the OTHER producer really does put the class in front.
        assert notebook_worker._failure_type(raised) != written

    def test_an_abandoned_row_lands_under_the_constant_that_named_it(
            self, client) -> None:
        """The other producer, and the one that does not go through
        `commit_extraction` at all: `settle_orphaned_running` UPDATEs the row
        in place. A census that only saw the INSERT path would miss it."""
        chat_id = seed(client, count=6)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="r1", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[], status="running")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            closed = notebook.settle_orphaned_running(con, chat_id)

        assert closed == 1, "ground: the row really was still running"
        assert _stats(chat_id)["error_types"] == {
            notebook.ABANDONED_IN_FLIGHT: 1}


class TestItReachesTheRoute:
    """C-5: a count that stops at the store is a count nobody can see."""

    def test_the_status_route_carries_the_breakdown(self, client) -> None:
        chat_id = seed(client, count=6)
        _settle(chat_id, "r1", "ReadTimeout")

        body = client.get("/api/v1/notebook/worker").json()

        assert body["stats"]["error_types"] == {"ReadTimeout": 1}

    def test_it_serialises_beside_the_counts_it_does_not_replace(
            self, client) -> None:
        """GROUND for the route: the three fields the panel already draws
        from are still there and still numbers. A new key that displaced one
        of them would pass the assertion above and break the card."""
        chat_id = seed(client, count=6)
        _settle(chat_id, "r1", notebook.ABANDONED_IN_FLIGHT)

        stats = client.get("/api/v1/notebook/worker").json()["stats"]

        assert stats["failed"] == 1
        assert stats["abandoned"] == 1
        assert stats["paid_and_lost"] == 1
        assert stats["error_types"] == {notebook.ABANDONED_IN_FLIGHT: 1}
