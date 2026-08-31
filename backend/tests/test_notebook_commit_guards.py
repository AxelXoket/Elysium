"""U-08 - what `commit_extraction` is and is not allowed to do.

Six wounds in one function, and every one of them is the extractor acting on
the reader's own material without being entitled to.

  * a model's suggestion could retire a note the READER typed, or one they
    PINNED, or one they had not looked at yet;
  * the same fact arriving twice was written down twice;
  * a `supersedes` intent formed in review mode was thrown away, so accepting
    the proposal an hour later left the note it replaced in the prompt;
  * a call the provider declined to price was recorded as free;
  * the same physical provider reply, settled twice, was billed twice.

Every test here drives the real function inside a real transaction against a
real database. Nothing about the storage layer is stubbed - the guards being
tested ARE SQL predicates, and a fake would just restate them.
"""
from __future__ import annotations

import notebook_store as notebook
from database import get_db

from tests.conftest import make_character, make_chat


def seed(client, count: int = 4) -> int:
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    with get_db() as con:
        for i in range(count):
            con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?,?,?)",
                (chat_id, "user" if i % 2 == 0 else "assistant", f"line {i}"))
    return chat_id


def fact(**over):
    base = {"text": "Her brother owns the mill.",
            "evidence": "her brother owns the mill",
            "kind": "fact", "durability": "permanent",
            "importance": 2, "supersedes": None}
    base.update(over)
    return base


def commit(chat_id, **kw):
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        return notebook.commit_extraction(
            con, chat_id=chat_id, from_id=1, to_id=4, **kw)


def review_mode() -> None:
    """Automatic acceptance OFF - the mode where a proposal waits."""
    import config
    from database import set_setting
    set_setting(config.SETTING_NOTEBOOK_AUTO_ACCEPT, "0")


def rows_by_id(chat_id):
    return {e["id"]: e for e in notebook.list_entries(chat_id)}


class TestWhatAModelMayRetire:
    def test_it_cannot_retire_a_note_the_reader_wrote(self, client) -> None:
        """The whole point of the notebook is that what the reader writes
        down stays. The retirement UPDATE named only the id."""
        chat_id = seed(client)
        mine = notebook.create_entry(chat_id, "The mill belonged to her uncle.")

        commit(chat_id, work_key="user-note", proposals=[fact(supersedes=0)],
               existing_ids=[mine["id"]])

        assert rows_by_id(chat_id)[mine["id"]]["retired_at"] is None

    def test_it_can_retire_a_note_it_wrote_itself(self, client) -> None:
        """POSITIVE CONTROL. Without this the guard could be a `WHERE 0=1`
        and every test above it would still pass."""
        chat_id = seed(client)
        theirs = notebook.create_entry(
            chat_id, "The mill belonged to her uncle.",
            provenance=notebook.PROV_MODEL)

        out = commit(chat_id, work_key="model-note",
                     proposals=[fact(supersedes=0)],
                     existing_ids=[theirs["id"]])

        assert out["retired"] == 1
        assert rows_by_id(chat_id)[theirs["id"]]["retired_at"] is not None

    def test_it_cannot_retire_a_pinned_note(self, client) -> None:
        """Pinning is the reader saying "this one, always"."""
        chat_id = seed(client)
        kept = notebook.create_entry(
            chat_id, "The mill belonged to her uncle.",
            provenance=notebook.PROV_MODEL, pinned=True)

        out = commit(chat_id, work_key="pinned", proposals=[fact(supersedes=0)],
                     existing_ids=[kept["id"]])

        assert out["retired"] == 0
        assert rows_by_id(chat_id)[kept["id"]]["retired_at"] is None

    def test_it_cannot_retire_a_suggestion_nobody_has_read(self, client):
        """The condition that makes the widening safe.

        The model now SEES its own pending suggestions, so it can name one in
        `supersedes` - and retiring one would remove a note from the review
        queue before the reader ever saw it.
        """
        chat_id = seed(client)
        pending = notebook.create_entry(
            chat_id, "The mill belonged to her uncle.",
            provenance=notebook.PROV_MODEL,
            status=notebook.STATUS_PROPOSED)

        out = commit(chat_id, work_key="pending",
                     proposals=[fact(supersedes=0)],
                     existing_ids=[pending["id"]])

        assert out["retired"] == 0
        assert rows_by_id(chat_id)[pending["id"]]["retired_at"] is None


class TestTheSameFactTwice:
    def test_the_second_copy_is_not_written(self, client) -> None:
        chat_id = seed(client)
        notebook.create_entry(chat_id, "Her brother owns the mill.")

        out = commit(chat_id, work_key="dup", proposals=[fact()],
                     existing_ids=[])

        assert out["written"] == 0
        assert out["duplicates"] == 1

    def test_punctuation_and_case_do_not_make_it_a_new_fact(self, client):
        chat_id = seed(client)
        notebook.create_entry(chat_id, "Her brother owns the mill.")

        out = commit(chat_id, work_key="dup2",
                     proposals=[fact(text="  her BROTHER owns the mill  ")],
                     existing_ids=[])

        assert out["written"] == 0

    def test_a_different_fact_still_gets_through(self, client) -> None:
        """GROUND CONTROL. A gate that refuses everything would satisfy both
        assertions above and silently end the feature."""
        chat_id = seed(client)
        notebook.create_entry(chat_id, "Her brother owns the mill.")

        out = commit(chat_id, work_key="fresh",
                     proposals=[fact(text="Her sister runs the ferry.")],
                     existing_ids=[])

        assert out["written"] == 1
        assert out["duplicates"] == 0

    def test_two_copies_in_ONE_reply_are_written_once(self, client) -> None:
        chat_id = seed(client)

        out = commit(chat_id, work_key="selfdup",
                     proposals=[fact(), fact()], existing_ids=[])

        assert out["written"] == 1
        assert out["duplicates"] == 1

    def test_a_retired_note_does_not_block_the_fact_coming_back(self, client):
        """Retired is not deleted, but it is not current either. A fact the
        reader superseded and that turns out to be true again has to be
        writable, or the notebook can never recover from a wrong retirement.
        """
        chat_id = seed(client)
        old = notebook.create_entry(chat_id, "Her brother owns the mill.")
        notebook.retire_entry(old["id"])

        out = commit(chat_id, work_key="revived", proposals=[fact()],
                     existing_ids=[])

        assert out["written"] == 1


class TestTheIntentSurvivesReviewMode:
    def test_accepting_a_proposal_retires_what_it_replaces(self, client):
        chat_id = seed(client)
        old = notebook.create_entry(
            chat_id, "The mill belonged to her uncle.",
            provenance=notebook.PROV_MODEL)
        review_mode()

        out = commit(chat_id, work_key="review",
                     proposals=[fact(supersedes=0)],
                     existing_ids=[old["id"]])
        assert out["accepted"] is False
        # Nothing retired yet - that is the correct review-mode behaviour.
        assert rows_by_id(chat_id)[old["id"]]["retired_at"] is None

        new_id = [e["id"] for e in notebook.list_entries(chat_id)
                  if e["status"] == notebook.STATUS_PROPOSED][0]
        r = client.post(f"/api/v1/notebook/entries/{new_id}/accept"
                        f"?chat_id={chat_id}")
        assert r.status_code == 200, r.text

        after = rows_by_id(chat_id)
        assert after[old["id"]]["retired_at"] is not None
        assert after[old["id"]]["superseded_by"] == new_id

    def test_accepting_one_with_no_intent_retires_nothing(self, client):
        """GROUND CONTROL: the accept route must not start retiring things on
        its own."""
        chat_id = seed(client)
        keep = notebook.create_entry(
            chat_id, "The mill belonged to her uncle.",
            provenance=notebook.PROV_MODEL)
        review_mode()

        commit(chat_id, work_key="plain", proposals=[fact()], existing_ids=[])
        new_id = [e["id"] for e in notebook.list_entries(chat_id)
                  if e["status"] == notebook.STATUS_PROPOSED][0]

        assert client.post(
            f"/api/v1/notebook/entries/{new_id}/accept"
            f"?chat_id={chat_id}").status_code == 200
        assert rows_by_id(chat_id)[keep["id"]]["retired_at"] is None


class TestTheMoney:
    def test_a_call_with_no_price_is_counted_as_unknown_not_free(self, client):
        chat_id = seed(client)

        commit(chat_id, work_key="nocost", proposals=[fact()],
               existing_ids=[],
               usage={"tokens_in": 10, "tokens_out": 5, "cost": None,
                      "request_id": "req-a"})

        with get_db() as con:
            today = notebook.spend_today(con)
        assert today["cost_unknown"] == 1
        assert today["cost"] == 0.0

    def test_a_priced_call_is_not_counted_as_unknown(self, client) -> None:
        """GROUND CONTROL for the counter."""
        chat_id = seed(client)

        commit(chat_id, work_key="priced", proposals=[fact()],
               existing_ids=[],
               usage={"tokens_in": 10, "tokens_out": 5, "cost": 0.25,
                      "request_id": "req-b"})

        with get_db() as con:
            today = notebook.spend_today(con)
        assert today["cost_unknown"] == 0
        assert today["cost"] == 0.25

    def test_the_same_provider_reply_is_billed_once(self, client) -> None:
        chat_id = seed(client)
        usage = {"tokens_in": 100, "tokens_out": 20, "cost": 0.5,
                 "request_id": "req-same"}

        commit(chat_id, work_key="first", proposals=[fact()],
               existing_ids=[], usage=usage)
        commit(chat_id, work_key="second",
               proposals=[fact(text="Her sister runs the ferry.")],
               existing_ids=[], usage=usage)

        with get_db() as con:
            today = notebook.spend_today(con)
        assert today["cost"] == 0.5
        assert today["tokens_in"] == 100

    def test_two_different_replies_are_billed_twice(self, client) -> None:
        """GROUND CONTROL: the ledger must not deduplicate real calls."""
        chat_id = seed(client)

        commit(chat_id, work_key="a", proposals=[fact()], existing_ids=[],
               usage={"tokens_in": 10, "tokens_out": 1, "cost": 0.25,
                      "request_id": "req-1"})
        commit(chat_id, work_key="b",
               proposals=[fact(text="Her sister runs the ferry.")],
               existing_ids=[],
               usage={"tokens_in": 10, "tokens_out": 1, "cost": 0.25,
                      "request_id": "req-2"})

        with get_db() as con:
            today = notebook.spend_today(con)
        assert today["cost"] == 0.5

    def test_a_reply_with_no_id_at_all_is_still_counted(self, client) -> None:
        """An unidentifiable call is still money. Dropping it would understate
        the total in the direction nobody would notice."""
        chat_id = seed(client)

        commit(chat_id, work_key="anon1", proposals=[fact()], existing_ids=[],
               usage={"tokens_in": 10, "tokens_out": 1, "cost": 0.25})
        commit(chat_id, work_key="anon2",
               proposals=[fact(text="Her sister runs the ferry.")],
               existing_ids=[],
               usage={"tokens_in": 10, "tokens_out": 1, "cost": 0.25})

        with get_db() as con:
            assert notebook.spend_today(con)["cost"] == 0.5
