"""The proxy's label: what an emptied box does, and how long a label may be.

Two defects meet in this one setting, and both were measured before this file
existed.

  1. An emptied name box DELETED the stored label and answered {"ok": true}.
     The proxy URL is write-only - cleared after every save, never shown again
     - so the label was the only readable trace of which proxy this is, and a
     later URL-only save could take it away by accident.

  2. It had NO length ceiling anywhere: not in the schema, not in the model,
     not in the route, and not in main.py's body-size middleware, which is
     scoped to /uploads/ and /tts/voices/. A 200,000-character label was
     accepted and read back whole. It was the only unbounded piece of user
     text in the application.

There is exactly one proxy - no `proxies` table, no {proxy_id} path segment -
so the sequence never advances past the first number. It still goes through
the shared helper so the prefix cannot drift from the one every other surface
uses.
"""
from __future__ import annotations

import pytest

import database
import default_names
from routers.settings import MAX_PROXY_ALIAS_LEN

_PROXY = "/api/v1/settings/proxy"
_ALIAS = "/api/v1/settings/proxy/alias"
_URL = "http://127.0.0.1:8888"

#: The key left out entirely, which is a third thing from "" and from null.
_OMITTED = object()


def _save(client, alias=_OMITTED, required=False):
    body = {"proxy_url": _URL, "proxy_required": required}
    if alias is not _OMITTED:
        body["proxy_alias"] = alias
    return client.post(_PROXY, json=body)


def _stored(client) -> str | None:
    resp = client.get("/api/v1/settings")
    assert resp.status_code == 200, resp.text
    return resp.json()["proxy_alias"]


@pytest.mark.parametrize("cleared", ["", "   ", None, _OMITTED])
def test_an_emptied_box_names_the_proxy(client, cleared):
    """The Q-46 defect, closed. All four shapes of nothing used to erase it."""
    assert _save(client, alias="work").status_code == 200
    assert _stored(client) == "work"

    assert _save(client, alias=cleared).status_code == 200

    assert _stored(client) == f"{default_names.PROXY} 1"


@pytest.mark.parametrize("cleared", ["", "   ", None, _OMITTED])
def test_the_alias_only_route_agrees_with_the_full_save(client, cleared):
    """Two doors onto one label. They used to disagree about everything they
    could disagree about, so each is driven separately."""
    assert _save(client, alias="work").status_code == 200

    body = {} if cleared is _OMITTED else {"proxy_alias": cleared}
    resp = client.post(_ALIAS, json=body)

    assert resp.status_code == 200, resp.text
    assert resp.json()["proxy_alias"] == f"{default_names.PROXY} 1"
    assert _stored(client) == f"{default_names.PROXY} 1"


def test_the_stored_label_is_never_the_empty_string(client):
    """The old code wrote "" and GET turned it back into null on the way out,
    which is how a delete could look like an absence. Read at the row."""
    assert _save(client, alias="").status_code == 200

    assert database.get_setting("proxy_alias") == f"{default_names.PROXY} 1"


def test_a_label_that_was_given_is_stored_exactly(client):
    """GROUND CONTROL. Without it, a route that answered every save with the
    default name would satisfy every assertion above."""
    assert _save(client, alias="  work  ").status_code == 200

    assert _stored(client) == "work"


def test_a_second_save_can_still_rewrite_the_label(client):
    """POSITIVE CONTROL for the ground control: the FIRST write landing does
    not show that a second one can."""
    assert _save(client, alias="work").status_code == 200

    assert _save(client, alias="spare").status_code == 200

    assert _stored(client) == "spare"


def test_a_label_at_the_ceiling_is_accepted(client):
    """The boundary from below. A cap that refused its own limit would be a
    cap one shorter than it says."""
    assert _save(client, alias="x" * MAX_PROXY_ALIAS_LEN).status_code == 200

    assert _stored(client) == "x" * MAX_PROXY_ALIAS_LEN


def test_a_label_one_past_the_ceiling_is_refused(client):
    resp = _save(client, alias="x" * (MAX_PROXY_ALIAS_LEN + 1))

    assert resp.status_code == 400
    assert resp.json()["detail"] == "proxy_alias_too_long"


def test_the_alias_only_route_has_the_same_ceiling(client):
    """A ceiling on one of two doors is not a ceiling. This was the shape of
    the earlier bug where the two routes disagreed about a misspelt field."""
    assert _save(client, alias="work").status_code == 200

    resp = client.post(
        _ALIAS, json={"proxy_alias": "x" * (MAX_PROXY_ALIAS_LEN + 1)})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "proxy_alias_too_long"


def test_a_refused_length_changes_nothing(client):
    """A refusal that had already written would be worse than no ceiling."""
    assert _save(client, alias="work").status_code == 200

    _save(client, alias="x" * (MAX_PROXY_ALIAS_LEN + 1))

    assert _stored(client) == "work"


def test_deleting_the_proxy_still_leaves_no_name(client):
    """The one place a cleared label is still correct: there is no longer a
    proxy for the name to belong to."""
    assert _save(client, alias="work").status_code == 200

    assert client.delete(_PROXY).status_code == 200

    assert _stored(client) is None
