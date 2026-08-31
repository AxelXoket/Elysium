"""U-36 - a refused turn used to leave writing in the vault.

Two independent faults on one request path, both with the same shape: work
was done, and recorded, before the request had been judged fit to run.

  (a) `max_tokens` was range-checked eighty lines AFTER it entered the budget
      arithmetic. `-5000` becomes `max_tokens_chars = -15000`, the guard two
      lines on is then false, `available` GROWS by fifteen thousand
      characters, the notebook ceiling inflates with it, and exclusion
      reasons computed against a ceiling that never existed are written to
      the vault. Then the request is refused with 422.

  (b) `record_exclusions` ran before `_assemble_messages`, which can still
      refuse the turn (`boundaries_do_not_fit`, `context_too_large`). So a
      turn nobody had left every note marked "not sent this turn".

MEASURED THROUGH THE ROUTE, deliberately. `test_notebook_context.py`'s
`_turn` helper simulates "what the router does on every message, in the order
it does it" - a hand-written copy of the order, which stays green no matter
what the router actually does. Order is the thing under test here, so the
test has to call the real thing.
"""
from __future__ import annotations

import pytest

from database import get_db

from tests.conftest import make_character, make_chat
import notebook_store as notebook


def _send(client, chat_id: int, params: dict, **extra):
    body = {"message": "hello", "model_id": "test/model-1",
            "generation_params": params}
    body.update(extra)
    
    return client.post(f"/api/v1/chats/{chat_id}/complete", json=body)


def reasons_in_vault(chat_id: int) -> dict[int, str | None]:
    with get_db() as con:
        return {r["id"]: r["excluded_reason"] for r in con.execute(
            "SELECT id, excluded_reason FROM notebook_entries "
            "WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()}


def crowded(client, n: int = 40, chars: int = 200) -> int:
    """A chat whose notebook cannot possibly fit, so there is something to
    record - and something to get wrong."""
    chat_id = make_chat(client, make_character(client))
    for i in range(n):
        notebook.create_entry(chat_id, text=f"n{i} " + "x" * chars)
    return chat_id


class TestARefusedRequestWritesNothing:
    def test_an_out_of_range_max_tokens_leaves_the_vault_untouched(
            self, client) -> None:
        chat_id = crowded(client)
        before = reasons_in_vault(chat_id)
        assert before, "ground: there are notes to write reasons on"

        resp = _send(client, chat_id, {"max_tokens": -5000})

        assert resp.status_code == 422
        assert resp.json()["detail"] == "invalid_gen_params", (
            "the app's own error code, not FastAPI's validation array")
        assert reasons_in_vault(chat_id) == before

    def test_a_valid_max_tokens_of_one_is_still_accepted(
            self, client, provider) -> None:
        """GROUND CONTROL for the bound itself. `1` is the smallest legal
        value; a fix that refused it would pass the test above and break the
        feature."""
        chat_id = crowded(client)

        resp = _send(client, chat_id, {"max_tokens": 1})

        # NOT `!= 422`: a wrong URL answers 405 and would satisfy that.
        assert resp.status_code == 200, resp.text


class TestReasonsAreWrittenOnlyForATurnThatHappened:
    def test_a_turn_refused_by_the_assembler_leaves_the_vault_untouched(
            self, client) -> None:
        """`context_too_large` comes out of `_assemble_messages`, which used
        to run AFTER the reasons were already written."""
        chat_id = crowded(client)
        before = reasons_in_vault(chat_id)
        assert before, "ground: there are notes to write reasons on"

        # A message that cannot fit the budget no matter what is trimmed:
        # history and the notebook can both be evicted, the user's own turn
        # cannot, which is what `context_too_large` is for.
        resp = _send(client, chat_id, {}, context_budget_tokens=512,
                     message="q" * 200_000)

        assert resp.status_code == 400
        assert resp.json()["detail"] in {"context_too_large",
                                         "boundaries_do_not_fit"}
        assert reasons_in_vault(chat_id) == before

    def test_a_turn_that_happens_does_write_the_reasons(
            self, client, provider) -> None:
        """POSITIVE CONTROL, and it is not optional: every assertion above is
        an absence, and an absence over a route that never writes reasons at
        all would be green for nothing.
        """
        chat_id = crowded(client)
        assert set(reasons_in_vault(chat_id).values()) == {None}

        resp = _send(client, chat_id, {})

        assert resp.status_code == 200, resp.text
        written = [r for r in reasons_in_vault(chat_id).values() if r]
        assert written, "the route never records a reason, so the absences "\
                        "above prove nothing"

    def test_a_quiet_turn_clears_a_reason_left_by_a_busy_one(
            self, client, provider) -> None:
        """The DO NOT TOUCH half, measured THROUGH THE ROUTE.

        `record_exclusions` is called unconditionally on purpose. Guarded on
        `excluded` being non-empty, the CLEARING half never runs on a turn
        where nothing was excluded, so once the pressure stops the rows keep
        a reason from an earlier turn forever and the panel reads "not sent"
        for notes that are being sent every single time. A badge that is
        wrong in the safe direction is worse than no badge.

        The suite already had this property - as a hand-written simulation of
        "what the router does, in the order it does it", which stays green
        whatever the router actually does. Moving the call is exactly the
        change that simulation cannot see, so this one calls the route.
        """
        chat_id = crowded(client)
        _send(client, chat_id, {})
        after_pressure = reasons_in_vault(chat_id)
        assert [r for r in after_pressure.values() if r], (
            "ground: the busy turn really did write reasons")

        # Down to two notes, which fit with room to spare.
        for entry_id in list(after_pressure)[2:]:
            notebook.delete_entry(entry_id, chat_id=chat_id)

        _send(client, chat_id, {})

        assert not [r for r in reasons_in_vault(chat_id).values() if r], (
            "a note that fits again still reads as not sent")

