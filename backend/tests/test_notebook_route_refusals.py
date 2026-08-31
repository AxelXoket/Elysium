"""U-31 - a refusal reaches the wire as a code, never as a 500.

Three route bodies did not use the `try / except NotebookError: _refuse` shape
their five neighbours in the same file already used. The domain layer refuses
things - a partial reorder, a chat that is gone - and those refusals arrived
as uncaught exceptions: a 500, with no catalogued code behind it, for an
ordinary user action.

`create_boundary` had the same hole from the other side: it never checked that
the chat existed, so the foreign key fired and an IntegrityError became the
500. Its sibling `create_entry` has had that gate since it was written.
"""
from __future__ import annotations

import notebook_store as notebook

from tests.conftest import make_character, make_chat

API = "/api/v1/notebook"


def a_chat(client) -> int:
    return make_chat(client, make_character(client))


class TestReorder:
    def test_a_partial_list_is_refused_with_a_code(self, client) -> None:
        chat_id = a_chat(client)
        notebook.create_entry(chat_id, "First.")
        notebook.create_entry(chat_id, "Second.")
        ids = [e["id"] for e in notebook.list_entries(chat_id)]

        r = client.post(f"{API}/{chat_id}/reorder",
                        json={"ordered_ids": ids[:1]})

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "notebook_reorder_incomplete"

    def test_a_complete_list_still_works(self, client) -> None:
        """GROUND CONTROL. Without it the fix is satisfied by a route that
        refuses everything."""
        chat_id = a_chat(client)
        notebook.create_entry(chat_id, "First.")
        notebook.create_entry(chat_id, "Second.")
        ids = [e["id"] for e in notebook.list_entries(chat_id)]

        r = client.post(f"{API}/{chat_id}/reorder",
                        json={"ordered_ids": list(reversed(ids))})

        assert r.status_code == 200, r.text
        after = [e["id"] for e in notebook.list_entries(chat_id)]
        assert after == list(reversed(ids))


class TestALimitForAChatThatIsGone:
    def test_it_is_refused_with_a_code_not_a_500(self, client) -> None:
        r = client.post(f"{API}/boundaries",
                        json={"label": "no gore",
                              "phrasing": "Avoid graphic injury.",
                              "severity": "hard", "chat_id": 999999})

        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "chat_not_found"

    def test_a_limit_for_a_real_chat_still_works(self, client) -> None:
        """GROUND CONTROL for the gate."""
        chat_id = a_chat(client)

        r = client.post(f"{API}/boundaries",
                        json={"label": "just here",
                              "phrasing": "Only in this chat.",
                              "severity": "soft", "chat_id": chat_id})

        assert r.status_code == 200, r.text
        assert r.json()["scope"] == "chat"

    def test_a_global_limit_needs_no_chat_at_all(self, client) -> None:
        """The gate must not start demanding a chat from a limit that has
        none - a global limit belongs to no conversation."""
        r = client.post(f"{API}/boundaries",
                        json={"label": "no gore",
                              "phrasing": "Avoid graphic injury.",
                              "severity": "hard"})

        assert r.status_code == 200, r.text
        assert r.json()["scope"] == "global"


class TestNoUserActionProducesA500:
    def test_every_refusal_on_these_routes_carries_a_catalogued_code(
            self, client) -> None:
        """The shape of the whole unit in one assertion.

        Each of these is a thing a person can do by hand, and each used to
        answer 500. A status in the 4xx range with a code behind it is the
        difference between "you did something the app refuses" and "the app
        broke".
        """
        chat_id = a_chat(client)
        notebook.create_entry(chat_id, "First.")

        calls = [
            client.post(f"{API}/{chat_id}/reorder", json={"ordered_ids": []}),
            client.post(f"{API}/boundaries",
                        json={"label": "x", "phrasing": "y",
                              "severity": "hard", "chat_id": 999999}),
            client.post(f"{API}/999999/use-global", json={"use_global": True}),
        ]

        for r in calls:
            assert 400 <= r.status_code < 500, (r.status_code, r.text)
            detail = r.json()["detail"]
            assert isinstance(detail, str) and detail.isidentifier(), detail
