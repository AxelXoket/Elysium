"""What a persona is called when nobody said - create and rename.

A blank display name used to be a 422, so the panel answered "Display name is
required." over a form somebody had already filled in everywhere else.

Every expected string is built from `default_names`, never retyped.
"""
from __future__ import annotations

import default_names


def _create(client, **body) -> dict:
    resp = client.post("/api/v1/personas", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _display_names(client) -> list[str]:
    resp = client.get("/api/v1/personas")
    assert resp.status_code == 200, resp.text
    return [row["display_name"] for row in resp.json()]


def test_every_shape_of_empty_name_is_created_and_numbered(client):
    made = [
        _create(client, display_name=""),
        _create(client, display_name="   "),
        _create(client, display_name=None),
        _create(client, description="no name at all"),
    ]

    assert [row["display_name"] for row in made] == [
        f"{default_names.PERSONA} {n}" for n in (1, 2, 3, 4)
    ]


def test_a_deleted_number_is_handed_out_again(client):
    _create(client)
    second = _create(client)
    _create(client)
    assert second["display_name"] == f"{default_names.PERSONA} 2"

    assert client.delete(f"/api/v1/personas/{second['id']}").status_code == 200

    assert _create(client)["display_name"] == f"{default_names.PERSONA} 2"


def test_a_hand_typed_number_is_not_walked_over(client):
    _create(client, display_name=f"{default_names.PERSONA} 5")

    assert _create(client)["display_name"] == f"{default_names.PERSONA} 1"


def test_a_differently_cased_name_still_holds_its_number(client):
    _create(client, display_name="new persona 1")

    assert _create(client)["display_name"] == f"{default_names.PERSONA} 2"


def test_an_emptied_name_box_is_a_rename_to_the_default(client):
    made = _create(client, display_name="Nova")

    resp = client.patch(f"/api/v1/personas/{made['id']}",
                        json={"display_name": "  "})

    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == f"{default_names.PERSONA} 1"


def test_a_rename_does_not_count_the_name_it_is_replacing(client):
    made = _create(client)
    assert made["display_name"] == f"{default_names.PERSONA} 1"

    resp = client.patch(f"/api/v1/personas/{made['id']}",
                        json={"display_name": ""})

    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == f"{default_names.PERSONA} 1"


def test_a_patch_that_omits_the_name_leaves_it_alone(client):
    """An ABSENT display name is a patch about the description, not an
    emptied box, and it must not rename anything."""
    made = _create(client, display_name="Nova")

    resp = client.patch(f"/api/v1/personas/{made['id']}",
                        json={"description": "changed"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "Nova"


def test_a_name_that_was_given_is_stored_exactly(client):
    """GROUND CONTROL."""
    made = _create(client, display_name="  Nova  ")

    assert made["display_name"] == "Nova"
    assert "Nova" in _display_names(client)


def test_unrelated_names_do_not_advance_the_sequence(client):
    """POSITIVE CONTROL."""
    _create(client, display_name="Nova")
    _create(client, display_name="a night with Aria")

    assert _create(client)["display_name"] == f"{default_names.PERSONA} 1"


def test_the_persona_sequence_is_its_own(client):
    """A character called "New Character 1" must not shift the personas."""
    assert client.post("/api/v1/characters",
                       json={"name": f"{default_names.CHARACTER} 1"}
                       ).status_code == 201

    assert _create(client)["display_name"] == f"{default_names.PERSONA} 1"
