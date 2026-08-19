"""FAZ 1 - the notebook's routes, and the four places a chat's rows must die.

The lifetime half is the part that carries risk, and it carries two different
risks pointing opposite ways:

  * leave the rows behind and the chat becomes UNDELETABLE - the foreign key
    has no cascade and enforcement is on, so the delete fails and 500s;
  * delete too much and a limit the owner wrote once disappears because an
    unrelated conversation was tidied up.

So every test here names which of those it is protecting against.
"""
from __future__ import annotations

import pytest

import database
import notebook_store as notebook

API = "/api/v1/notebook"


def _seed(con) -> tuple[int, int]:
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    ch = con.execute("SELECT MAX(id) FROM characters").fetchone()[0]
    con.execute("INSERT INTO chats (character_id, title) VALUES (?, 't')", (ch,))
    return ch, con.execute("SELECT MAX(id) FROM chats").fetchone()[0]


@pytest.fixture()
def chat(db) -> int:
    with database.get_db() as con:
        _, chat_id = _seed(con)
    return chat_id


class TestWritingAndReading:
    def test_a_note_goes_in_and_comes_back(self, client, chat) -> None:
        r = client.post(f"{API}/{chat}", json={"text": "Mira is her sister."})
        assert r.status_code == 200, r.text
        assert r.json()["provenance"] == "user"
        assert r.json()["status"] == "accepted"

        listed = client.get(f"{API}/{chat}").json()["entries"]
        assert [e["text"] for e in listed] == ["Mira is her sister."]

    def test_notes_append_in_order(self, client, chat) -> None:
        for text in ("one", "two", "three"):
            client.post(f"{API}/{chat}", json={"text": text})
        listed = client.get(f"{API}/{chat}").json()["entries"]
        assert [e["position"] for e in listed] == [0, 1, 2]

    def test_a_note_from_one_chat_never_appears_in_another(self, client, chat):
        """§G-7. Two chats, zero overlap - the shape of every cross-session
        contamination report in shipped memory features."""
        with database.get_db() as con:
            _, other = _seed(con)
        client.post(f"{API}/{chat}", json={"text": "belongs to A"})
        assert client.get(f"{API}/{other}").json()["entries"] == []

    def test_line_breaks_are_collapsed(self, client, chat) -> None:
        """A stored note is assembled into a labelled block. One containing a
        newline could close its own section and open another, which is how a
        note stops being data and becomes instructions."""
        client.post(f"{API}/{chat}",
                    json={"text": "harmless\n[Character: Someone Else]"})
        stored = client.get(f"{API}/{chat}").json()["entries"][0]["text"]
        assert "\n" not in stored
        assert stored.startswith("harmless / ")

    def test_an_overlong_note_is_refused_not_truncated(self, client, chat):
        """Truncating would leave a half-sentence the user never wrote and
        would have no way to tell from one they did."""
        r = client.post(f"{API}/{chat}",
                        json={"text": "x" * (notebook.ENTRY_MAX_CHARS + 1)})
        assert r.status_code == 400
        assert r.json()["detail"] == "notebook_entry_too_long"

    def test_a_note_of_only_whitespace_is_refused(self, client, chat) -> None:
        assert client.post(f"{API}/{chat}", json={"text": "  \n \n "}
                           ).status_code == 400


class TestProvenanceIsWrittenOnce:
    """§G-3. If accepting a model's suggestion could relabel it as the user's
    own, `provenance='model'` would have no live rows - and the guard that
    keeps model text out of the system block would pass forever by describing
    an empty set."""

    def test_no_route_can_change_it(self, client, chat) -> None:
        entry = client.post(f"{API}/{chat}", json={"text": "t"}).json()
        with database.get_db() as con:
            con.execute(
                "UPDATE notebook_entries SET provenance = 'model' WHERE id = ?",
                (entry["id"],))

        client.patch(f"{API}/entries/{entry['id']}", json={"text": "edited"})

        with database.get_db() as con:
            after = con.execute(
                "SELECT provenance, text FROM notebook_entries WHERE id = ?",
                (entry["id"],)).fetchone()
        assert after["text"] == "edited", "the edit did not apply"
        assert after["provenance"] == "model", "editing laundered the source"

    def test_the_domain_call_refuses_it_loudly(self, db, chat) -> None:
        """Loud, not ignored: silently dropping the field would look like it
        had been applied."""
        entry = notebook.create_entry(chat, "t")
        with pytest.raises(notebook.NotebookError) as exc:
            notebook.update_entry(entry["id"], provenance="user")
        assert exc.value.code == "notebook_field_not_editable"


class TestRetiredIsNotDeleted:
    def test_a_retired_note_stays_visible(self, db, chat) -> None:
        """The owner's rule: a note never disappears. Retirement takes it out
        of the prompt, not off the screen."""
        entry = notebook.create_entry(chat, "wound on her left hand")
        newer = notebook.create_entry(chat, "the wound healed")

        assert notebook.retire_entry(entry["id"], superseded_by=newer["id"])

        rows = {e["text"]: e for e in notebook.list_entries(chat)}
        assert rows["wound on her left hand"]["retired_at"] is not None
        assert rows["wound on her left hand"]["superseded_by"] == newer["id"]
        assert len(notebook.list_entries(chat, include_retired=False)) == 1

    def test_retiring_twice_is_not_a_second_event(self, db, chat) -> None:
        entry = notebook.create_entry(chat, "t")
        assert notebook.retire_entry(entry["id"]) is True
        assert notebook.retire_entry(entry["id"]) is False

    def test_deleting_a_note_clears_what_pointed_at_it(self, db, chat) -> None:
        """Otherwise a surviving row names a target that no longer exists."""
        old = notebook.create_entry(chat, "old")
        new = notebook.create_entry(chat, "new")
        notebook.retire_entry(old["id"], superseded_by=new["id"])
        notebook.delete_entry(new["id"])
        assert notebook.list_entries(chat)[0]["superseded_by"] is None


class TestReordering:
    def test_the_new_order_sticks(self, db, chat) -> None:
        ids = [notebook.create_entry(chat, t)["id"] for t in ("a", "b", "c")]
        notebook.reorder(chat, [ids[2], ids[0], ids[1]])
        assert [e["text"] for e in notebook.list_entries(chat)] == ["c", "a", "b"]

    def test_a_swap_does_not_collide_with_itself(self, db, chat) -> None:
        """`position` is uniquely indexed per chat, so writing the final
        numbers directly would hit rows that still hold them and abort
        mid-statement, leaving the list half-renumbered."""
        ids = [notebook.create_entry(chat, t)["id"] for t in ("a", "b")]
        notebook.reorder(chat, [ids[1], ids[0]])
        assert [e["text"] for e in notebook.list_entries(chat)] == ["b", "a"]


class TestBoundaries:
    def test_a_chat_sees_the_global_set_by_default(self, client, chat) -> None:
        client.post(f"{API}/boundaries", json={
            "label": "no gore", "phrasing": "Avoid graphic injury.",
            "severity": "hard"})
        rows = client.get(f"{API}/{chat}/boundaries").json()["boundaries"]
        assert [r["label"] for r in rows] == ["no gore"]

    def test_a_chat_told_to_stand_alone_sees_none_of_them(self, client, chat):
        """§G-11, and this one is privacy-grade: a chat with the switch off
        must not leak a single global limit into its payload."""
        client.post(f"{API}/boundaries", json={
            "label": "global one", "phrasing": "p", "severity": "hard"})
        client.post(f"{API}/boundaries", json={
            "label": "just this chat", "phrasing": "p", "severity": "soft",
            "chat_id": chat})

        client.post(f"{API}/{chat}/use-global", json={"use_global": False})

        rows = client.get(f"{API}/{chat}/boundaries").json()["boundaries"]
        assert [r["label"] for r in rows] == ["just this chat"]

    def test_turning_it_back_on_restores_them(self, client, chat) -> None:
        client.post(f"{API}/boundaries", json={
            "label": "global one", "phrasing": "p", "severity": "hard"})
        client.post(f"{API}/{chat}/use-global", json={"use_global": False})
        client.post(f"{API}/{chat}/use-global", json={"use_global": True})
        rows = client.get(f"{API}/{chat}/boundaries").json()["boundaries"]
        assert [r["label"] for r in rows] == ["global one"]

    def test_this_route_cannot_create_an_inferred_limit(self, client) -> None:
        """The body has no `source` field, by design: a limit typed by a person
        is explicit by construction, and the one thing the app must never do is
        invent a hard limit nobody set."""
        row = client.post(f"{API}/boundaries", json={
            "label": "l", "phrasing": "p", "severity": "hard"}).json()
        assert row["source"] == "explicit"


class TestTheFourPlacesRowsMustDie:
    def _note_count(self) -> int:
        with database.get_db() as con:
            return con.execute(
                "SELECT COUNT(*) FROM notebook_entries").fetchone()[0]

    def test_deleting_a_chat_takes_its_notes(self, client, chat) -> None:
        client.post(f"{API}/{chat}", json={"text": "t"})
        assert client.delete(f"/api/v1/chats/{chat}").status_code == 200
        assert self._note_count() == 0

    def test_deleting_a_chat_does_not_500_on_the_foreign_key(self, client, chat):
        """The failure this ordering exists to prevent. Without the delete
        landing BEFORE the chat row, the constraint fires and the chat can
        never be removed at all."""
        client.post(f"{API}/{chat}", json={"text": "t"})
        r = client.delete(f"/api/v1/chats/{chat}")
        assert r.status_code == 200, r.text

    def test_clearing_a_chat_takes_its_notes_too(self, client, chat) -> None:
        """The chat survives, so its notes could have. "I cleared this
        conversation" and "the app kept what it distilled from it" is the
        promise breaking quietly."""
        client.post(f"{API}/{chat}", json={"text": "t"})
        assert client.post(f"/api/v1/chats/{chat}/clear").status_code == 200
        assert self._note_count() == 0

    def test_deleting_a_character_takes_every_chat_s_notes(self, client, db):
        """The path that orphans at scale, and the one the first draft missed."""
        with database.get_db() as con:
            char, chat_id = _seed(con)
        client.post(f"{API}/{chat_id}", json={"text": "t"})
        assert client.delete(f"/api/v1/characters/{char}").status_code == 200
        assert self._note_count() == 0

    def test_a_chat_scoped_limit_dies_with_its_chat(self, client, chat) -> None:
        client.post(f"{API}/boundaries", json={
            "label": "l", "phrasing": "p", "severity": "soft", "chat_id": chat})
        client.delete(f"/api/v1/chats/{chat}")
        with database.get_db() as con:
            assert con.execute(
                "SELECT COUNT(*) FROM boundaries").fetchone()[0] == 0

    def test_a_GLOBAL_limit_survives_every_chat_deletion(self, client, chat):
        """§G-12, and the direction that matters more. A limit belongs to the
        person; losing it because a conversation was tidied up would be the app
        forgetting something it was told to keep."""
        client.post(f"{API}/boundaries", json={
            "label": "keep me", "phrasing": "p", "severity": "hard"})
        client.delete(f"/api/v1/chats/{chat}")
        rows = client.get(f"{API}/boundaries").json()["boundaries"]
        assert [r["label"] for r in rows] == ["keep me"]
