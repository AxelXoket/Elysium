"""The daily ceiling - which was a number nothing read.

`NOTEBOOK_DAILY_CALL_CAP` existed in config.py with a comment stating it was
"enforced as a BLOCK before the call rather than an alert after it", and no
code in the repository imported it. That is worse than having no cap: in
review it reads as a control. The one path that actually spent money had no
ceiling of any kind, and it is reachable by pressing a button.

The comment it carries is also the design rationale for the prompt bounds
tested in test_notebook_extract_hardening.py: the largest documented runaway
in this space was not a loop, it was a context that grew every call while a
budget alarm dutifully fired.
"""
from __future__ import annotations

import pytest

import config
import notebook_store as notebook
from database import get_db


class TestTheClaimIsMadeBeforeTheCallNotAfterIt:
    def test_the_first_call_of_the_day_is_allowed(self, db) -> None:
        with get_db() as con:
            assert notebook.claim_call(con, 60) == 1

    def test_calls_accumulate(self, db) -> None:
        with get_db() as con:
            for expected in (1, 2, 3):
                assert notebook.claim_call(con, 60) == expected

    def test_the_ceiling_refuses(self, db) -> None:
        with get_db() as con:
            notebook.claim_call(con, 2)
            notebook.claim_call(con, 2)
            with pytest.raises(notebook.NotebookError) as exc:
                notebook.claim_call(con, 2)
        assert exc.value.code == "notebook_daily_cap_reached"

    def test_a_cap_of_zero_refuses_the_very_first_call(self, db) -> None:
        with get_db() as con:
            with pytest.raises(notebook.NotebookError):
                notebook.claim_call(con, 0)

    def test_a_failed_call_still_counts(self, db) -> None:
        """The whole reason the claim is separate from the recording. A
        counter incremented on success bounds nothing: failed calls are billed
        too, and a failing model is the one a retry loop calls hardest."""
        with get_db() as con:
            notebook.claim_call(con, 60)
            # ...the request now blows up and record_usage is never reached.
            assert notebook.spend_today(con)["calls"] == 1


class TestWhatItCost:
    def test_nothing_spent_reads_as_zero_not_as_missing(self, db) -> None:
        with get_db() as con:
            assert notebook.spend_today(con) == {
                "calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}

    def test_usage_accumulates_across_calls(self, db) -> None:
        with get_db() as con:
            notebook.claim_call(con, 60)
            notebook.record_usage(con, {"tokens_in": 900, "tokens_out": 40,
                                        "cost": 0.00007})
            notebook.record_usage(con, {"tokens_in": 100, "tokens_out": 10,
                                        "cost": 0.00003})
            got = notebook.spend_today(con)
        assert got["calls"] == 1
        assert got["tokens_in"] == 1000
        assert got["cost"] == pytest.approx(0.0001)

    def test_a_reply_with_no_usage_block_does_not_crash_the_ledger(self, db):
        """OpenRouter returns usage automatically, but "automatically" is a
        property of today's API and a null here would otherwise abort the
        transaction that had already spent the money."""
        with get_db() as con:
            notebook.record_usage(con, {"tokens_in": None, "tokens_out": None,
                                        "cost": None})
            assert notebook.spend_today(con)["cost"] == 0.0


class TestTheDayColumnCannotBeNull:
    def test_the_schema_says_not_null(self, db) -> None:
        """SQLite's legacy quirk lets a non-INTEGER PRIMARY KEY hold unlimited
        NULLs. The defence was written on the neighbouring table, with a
        comment explaining exactly this, and not applied here - so a `day`
        computed as None would accumulate rows the daily total can never find,
        and the cap would pass forever by summing nothing."""
        with get_db() as con:
            cols = {r[1]: r for r in
                    con.execute("PRAGMA table_info(notebook_spend)").fetchall()}
        assert cols["day"][3] == 1, "day is nullable"

    def test_a_null_day_is_rejected_by_the_database(self, db) -> None:
        # The driver is SQLCipher's, not the stdlib's - asserting on
        # sqlite3.IntegrityError here would pass for the wrong reason.
        import database
        with get_db() as con:
            with pytest.raises(database.sqlite3.IntegrityError):
                con.execute("INSERT INTO notebook_spend (day) VALUES (NULL)")


class TestTheRouteIsWiredToTheLedger:
    def test_the_dry_run_refuses_once_the_day_is_spent(
            self, client, monkeypatch) -> None:
        """The block, on the path that actually spends. Without it the route
        could be called ten thousand times and would make ten thousand billed
        requests."""
        import database
        import openrouter

        from tests.conftest import make_character, make_chat

        chat_id = make_chat(client, make_character(client))
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        sent = []

        async def spy(*a, **kw):
            sent.append(1)
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"content": '{"facts": []}'}}]}

        monkeypatch.setattr(openrouter, "complete", spy)
        monkeypatch.setattr(config, "NOTEBOOK_DAILY_CALL_CAP", 0)

        resp = client.post(f"/api/v1/notebook/{chat_id}/extract/dry-run")
        assert resp.status_code == 429
        assert resp.json()["detail"] == "notebook_daily_cap_reached"
        assert sent == [], "a refused call must not reach the provider"
