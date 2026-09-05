"""What a character is called when nobody said - create, import and rename.

All three doors used to refuse. `POST /characters` answered 422 whatever the
dialog did, `POST /characters/import` threw a whole card away over one absent
field, and `PATCH` refused an emptied box. None of them can now.

Every expected string is built from `default_names`, never retyped.
"""
from __future__ import annotations

import default_names


def _create(client, **body) -> dict:
    resp = client.post("/api/v1/characters", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _names(client) -> list[str]:
    resp = client.get("/api/v1/characters")
    assert resp.status_code == 200, resp.text
    return [row["name"] for row in resp.json()]


def test_every_shape_of_empty_name_is_created_and_numbered(client):
    """Four inputs that used to be four 422s: empty, spaces, null, absent."""
    made = [
        _create(client, name=""),
        _create(client, name="   "),
        _create(client, name=None),
        _create(client, description="no name at all"),
    ]

    assert [row["name"] for row in made] == [
        f"{default_names.CHARACTER} {n}" for n in (1, 2, 3, 4)
    ]


def test_a_deleted_number_is_handed_out_again(client):
    first = _create(client)
    second = _create(client)
    _create(client)
    assert second["name"] == f"{default_names.CHARACTER} 2"

    assert client.delete(
        f"/api/v1/characters/{second['id']}").status_code == 200

    assert _create(client)["name"] == f"{default_names.CHARACTER} 2"
    assert first["name"] == f"{default_names.CHARACTER} 1"


def test_a_hand_typed_number_is_not_walked_over(client):
    _create(client, name=f"{default_names.CHARACTER} 5")

    assert _create(client)["name"] == f"{default_names.CHARACTER} 1"


def test_a_differently_cased_name_still_holds_its_number(client):
    _create(client, name="new character 1")

    assert _create(client)["name"] == f"{default_names.CHARACTER} 2"


def test_an_imported_card_with_no_name_is_kept(client):
    """The import used to answer 400 `character_name_required` and discard a
    description, a greeting and example dialogue along with it."""
    resp = client.post("/api/v1/characters/import",
                       json={"description": "no name on this card",
                             "first_mes": "Hello."})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == f"{default_names.CHARACTER} 1"
    assert body["description"] == "no name on this card"


def test_an_imported_card_shares_the_sequence_with_created_ones(client):
    """One sequence per SURFACE, not per route: both write the same column, so
    a card imported after a character was created takes the next number."""
    _create(client)

    resp = client.post("/api/v1/characters/import", json={"name": "  "})

    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == f"{default_names.CHARACTER} 2"


def test_an_emptied_name_box_is_a_rename_to_the_default(client):
    made = _create(client, name="Aria")

    resp = client.patch(f"/api/v1/characters/{made['id']}", json={"name": ""})

    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == f"{default_names.CHARACTER} 1"


def test_a_rename_does_not_count_the_name_it_is_replacing(client):
    made = _create(client)
    assert made["name"] == f"{default_names.CHARACTER} 1"

    resp = client.patch(f"/api/v1/characters/{made['id']}", json={"name": "  "})

    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == f"{default_names.CHARACTER} 1"


def test_a_patch_that_omits_the_name_leaves_it_alone(client):
    """The other empty. An ABSENT name is not an emptied box - it is a patch
    about some other field - and it must not rename anything."""
    made = _create(client, name="Aria")

    resp = client.patch(f"/api/v1/characters/{made['id']}",
                        json={"description": "changed"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Aria"


def test_a_name_that_was_given_is_stored_exactly(client):
    """GROUND CONTROL: a filled-in name is kept, trimmed, never replaced."""
    made = _create(client, name="  Aria  ")

    assert made["name"] == "Aria"
    assert "Aria" in _names(client)


def test_unrelated_names_do_not_advance_the_sequence(client):
    """POSITIVE CONTROL."""
    _create(client, name="Aria")
    _create(client, name="a night with Aria")

    assert _create(client)["name"] == f"{default_names.CHARACTER} 1"
