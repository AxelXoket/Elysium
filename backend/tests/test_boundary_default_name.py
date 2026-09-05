"""What a notebook limit is called when nobody said.

The label is what the panel PRINTS; the phrasing is what the limit MEANS. So
only the label gets a default: a limit with no phrasing has nothing to
enforce and is still refused with `boundary_empty`.

Every expected string is built from `default_names`, never retyped.
"""
from __future__ import annotations

import default_names

_BOUNDARIES = "/api/v1/notebook/boundaries"


def _create(client, **body) -> dict:
    payload = {"phrasing": "no needles on screen", "severity": "soft"}
    payload.update(body)
    resp = client.post(_BOUNDARIES, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _labels(client) -> list[str]:
    resp = client.get(_BOUNDARIES)
    assert resp.status_code == 200, resp.text
    return [row["label"] for row in resp.json()["boundaries"]]


def test_every_shape_of_empty_label_is_created_and_numbered(client):
    made = [
        _create(client, label=""),
        _create(client, label="   "),
        _create(client),
    ]

    assert [row["label"] for row in made] == [
        f"{default_names.BOUNDARY} {n}" for n in (1, 2, 3)
    ]


def test_an_empty_phrasing_is_still_refused(client):
    """The asymmetry, as its own assertion. A label can be generated; a
    meaning cannot."""
    resp = client.post(_BOUNDARIES,
                       json={"label": "no needles", "phrasing": "   ",
                             "severity": "soft"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "boundary_empty"


def test_a_deleted_number_is_handed_out_again(client):
    _create(client)
    second = _create(client)
    _create(client)
    assert second["label"] == f"{default_names.BOUNDARY} 2"

    assert client.delete(
        f"{_BOUNDARIES}/{second['id']}").status_code == 200

    assert _create(client)["label"] == f"{default_names.BOUNDARY} 2"


def test_a_differently_cased_label_still_holds_its_number(client):
    _create(client, label="new boundary 1")

    assert _create(client)["label"] == f"{default_names.BOUNDARY} 2"


def test_a_label_that_was_given_is_stored_exactly(client):
    """GROUND CONTROL."""
    made = _create(client, label="  no needles  ")

    assert made["label"] == "no needles"
    assert "no needles" in _labels(client)


def test_unrelated_labels_do_not_advance_the_sequence(client):
    """POSITIVE CONTROL."""
    _create(client, label="no needles")
    _create(client, label="a night with Aria")

    assert _create(client)["label"] == f"{default_names.BOUNDARY} 1"
