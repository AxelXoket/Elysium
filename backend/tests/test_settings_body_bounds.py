"""Two settings bodies that let a request do damage the route never meant.

Both defects have the same shape: the schema was the only place that could
have refused the request, and it did not, so the route accepted something it
then had to cope with.

    POST /settings/stop-sequences - the clamp bounds what is KEPT. Nothing
    bounded what is READ. `break` fires on `len(cleaned) >= MAX_STOP_SEQUENCES`,
    so five million empty entries keep nothing and five million copies of one
    entry keep one; neither reaches four, so neither trips the clamp and both
    are walked in full on the event loop. Measured: a 20 MB body of five
    million entries answered 200 with the loop held for 0.327s by the walk,
    plus a further 0.116s to parse the body, which no entry ceiling removes.

    POST /settings/proxy - the body took unknown fields. `proxy_alias` is
    optional, so the two required fields spelled correctly plus the label
    spelled `proxy_allias` reached the route as `proxy_alias=None`; the route
    wrote "", the stored label was deleted, and the answer was `{"ok": true}`.
    The sibling route POST /settings/proxy/alias refused the same typo with 422.

HOW THE WALK IS COUNTED, because it is the only unusual thing here. The
trimming loop reads MAX_STOP_SEQUENCE_CHARS from the module on every pass, as
the upper bound of a slice, and a slice bound is taken through `__index__`.
Standing an object in for that constant counts passes exactly, from outside,
without the route being changed and without any test reading a line of source.
The count is the honest instrument for this route: a stopwatch cannot separate
the walk from the JSON parse, which happens on the loop either way and which
no entry ceiling can remove.

WHAT IS NOT BOUNDED, so that nobody reads more into these tests than they say.
The ceiling counts entries, not bytes, and a stop sequence is trimmed rather
than refused, so an accepted 64-entry body can still be enormous and is still
buffered and parsed on the loop. And whether an emptied alias box should clear
the stored label or leave it alone is not settled here; the clearing tests
below hold today's answer still across the tightening, they do not choose it.
"""
from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

import database
import default_names
import routers.settings as settings
from routers.settings import (
    MAX_STOP_SEQUENCES,
    MAX_STOP_SEQUENCES_READ,
    MAX_STOP_SEQUENCE_CHARS,
    StopSequencesBody,
)

from tests.loop_guard import (
    MAX_FREEZE_S,
    STALL_S,
    longest_freeze as _longest_freeze,
)

_STOP_SEQUENCES = "/api/v1/settings/stop-sequences"
_PROXY = "/api/v1/settings/proxy"
_PROXY_ALIAS = "/api/v1/settings/proxy/alias"

#: Any URL the validator accepts; nothing here dials it.
_URL = "http://127.0.0.1:9"

#: Sentinel for "the key is not in the body at all", which is a different
#: input from `None` on the wire even though this route treats them alike.
_OMITTED = object()


def _proxy_body(alias=_OMITTED, required=False) -> dict:
    body = {"proxy_url": _URL, "proxy_required": required}
    if alias is not _OMITTED:
        body["proxy_alias"] = alias
    return body


class _PassCounter:
    """Stands in for MAX_STOP_SEQUENCE_CHARS and counts loop passes.

    Returns the real value, so trimming behaves exactly as it does in
    production and the assertions about the saved list stay meaningful.
    """

    def __init__(self, value: int) -> None:
        self._value = value
        self.passes = 0

    def __index__(self) -> int:
        self.passes += 1
        return self._value


@pytest.fixture()
def walk(monkeypatch):
    counter = _PassCounter(MAX_STOP_SEQUENCE_CHARS)
    monkeypatch.setattr(settings, "MAX_STOP_SEQUENCE_CHARS", counter)
    return counter


# -- H-48: the body the route will read -------------------------------------


def test_the_route_walks_every_entry_it_is_handed(client, walk):
    """Positive control for the absence assertion below, and the trap itself.

    Repeated entries keep only the first, so the clamp is never reached and the
    early exit never fires: the route walks the whole body. The counter
    therefore reads the body length whenever a walk happens, which is what
    makes a later reading of zero mean "no walk" rather than "no instrument".

    It is also the test that keeps the instrument honest. The counter sees a
    slice bound being taken, not an entry being read, so hoisting
    MAX_STOP_SEQUENCE_CHARS into a local above the loop would leave the zero
    reading below true and empty. This assertion is what goes red for that.
    """
    resp = client.post(_STOP_SEQUENCES,
                       json={"stop_sequences": ["A"] * MAX_STOP_SEQUENCES_READ})

    assert resp.status_code == 200, resp.text
    assert resp.json()["stop_sequences"] == ["A"]
    assert walk.passes == MAX_STOP_SEQUENCES_READ


def test_a_body_over_the_read_ceiling_never_reaches_the_walk(client, walk):
    """The refusal has to happen before the loop, not inside it.

    A status code alone would not say that: a route that walked five million
    entries and then answered 422 would pass it. The pass counter is what
    distinguishes the two, and the test above is what proves the counter can
    see a walk at all.
    """
    kept = client.post(_STOP_SEQUENCES, json={"stop_sequences": ["Human:"]})
    assert kept.status_code == 200, kept.text
    # Ground: the route is reachable, the instrument is armed, and there is a
    # stored value that a silent acceptance could overwrite.
    assert walk.passes > 0
    before = database.get_setting("stop_sequences")
    walk.passes = 0

    over = ["A"] * (MAX_STOP_SEQUENCES_READ + 1)
    resp = client.post(_STOP_SEQUENCES, json={"stop_sequences": over})

    # The walk first, because it is the claim: a route that spun through all
    # of it and then answered 422 would satisfy the status line below.
    assert walk.passes == 0, f"the loop was walked {walk.passes} times anyway"
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "validation_failed"
    assert database.get_setting("stop_sequences") == before


@pytest.mark.parametrize("size", [
    MAX_STOP_SEQUENCES,
    MAX_STOP_SEQUENCES + 3,
    MAX_STOP_SEQUENCES_READ,
])
def test_the_ceiling_leaves_the_clamp_alone(client, size):
    """Ground control for the whole unit: the ceiling must not become the
    clamp. A stale dialog sending more entries than are kept is still clamped
    and saved, never refused. The middle size restates, against the imported
    ceiling, what test_proxy.py already pins with a literal seven, because a
    badly chosen ceiling is exactly what would break it.
    """
    resp = client.post(_STOP_SEQUENCES,
                       json={"stop_sequences": [f"S{n}:" for n in range(size)]})

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["stop_sequences"]) == min(size, MAX_STOP_SEQUENCES)


def test_a_body_with_no_list_at_all_is_still_refused(client):
    """The field carried no default before and must carry none now.

    `Field(...)` keeps it required, but only as long as nobody gives it a
    default while tuning the ceiling. With `default=[]` an empty body would
    clear the stored list and answer 200, which is the neighbouring defect.
    """
    assert client.post(_STOP_SEQUENCES,
                       json={"stop_sequences": ["Human:"]}).status_code == 200

    resp = client.post(_STOP_SEQUENCES, json={})

    assert resp.status_code == 422, resp.text
    assert database.get_setting("stop_sequences") == '["Human:"]'


@pytest.mark.anyio
async def test_the_worst_entry_count_the_schema_admits_stays_off_the_loop(
    anyio_backend, client
):
    """Worst case by count: every entry empty, so nothing is kept, the clamp
    never fires and the loop runs its full length. Built through the schema
    rather than around it, so the second half is the same statement about count
    that the route relies on - nothing with more entries can be built at all.

    Read the name literally. This binds the COUNT dimension and nothing else,
    and it binds it loosely. Measured here at the ceiling raised to two
    million: 0.065s, so an empty entry costs about 32ns and this only goes red
    somewhere above one and a half million of them. Nothing in this file pins
    the ceiling anywhere near where it is set; every test here would still
    pass at a million. The byte dimension is not bounded by the ceiling at all
    and is not measured here.
    """
    body = StopSequencesBody(stop_sequences=[""] * MAX_STOP_SEQUENCES_READ)

    freeze, out = await _longest_freeze(settings.save_stop_sequences(body))

    assert out["stop_sequences"] == []
    assert freeze < MAX_FREEZE_S

    with pytest.raises(ValidationError):
        StopSequencesBody(stop_sequences=[""] * (MAX_STOP_SEQUENCES_READ + 1))


@pytest.mark.anyio
async def test_the_freeze_meter_reports_a_freeze_when_there_is_one(
    anyio_backend,
):
    """Positive control for the absence assertion above. Without this, a meter
    that always read zero would prove the ceiling safe whatever it was set to.
    """

    async def blocks_the_loop():
        time.sleep(STALL_S)
        return {"stop_sequences": []}

    freeze, _ = await _longest_freeze(blocks_the_loop())

    assert freeze >= MAX_FREEZE_S


# -- H-49: the body the proxy route will accept -----------------------------


def test_a_well_formed_proxy_save_stores_the_label(client):
    """Ground control: the shape the app sends is untouched by the tightening."""
    resp = client.post(_PROXY, json=_proxy_body(alias="work"))

    assert resp.status_code == 200, resp.text
    assert database.get_setting("proxy_alias") == "work"


def test_a_misspelled_label_is_refused_and_nothing_is_written(client):
    """The defect: one wrong letter in an OPTIONAL field name deleted a saved
    value and answered `{"ok": true}`. Two required fields do not protect the
    third; only refusing unknown names does.

    The kill-switch is flipped in the same refused body, so a route that
    applied half of it before refusing the rest would be caught too.
    """
    assert client.post(_PROXY, json=_proxy_body(alias="work")).status_code == 200
    assert database.get_setting("proxy_alias") == "work"
    assert database.get_setting("proxy_required") == "0"

    typo = {"proxy_url": _URL, "proxy_required": True, "proxy_allias": "spare"}
    answer = client.post(_PROXY, json=typo)

    # The stored values first, because they are the damage; the status code is
    # only how the caller finds out.
    assert database.get_setting("proxy_alias") == "work", answer.text
    assert database.get_setting("proxy_required") == "0", answer.text
    assert answer.status_code == 422, answer.text
    assert answer.json()["detail"] == "validation_failed"


def test_an_unknown_field_that_is_not_a_near_miss_is_refused_too(client):
    """Otherwise the pair above would only prove that one typo is caught,
    which a field-name allowlist for `proxy_allias` would also satisfy. The
    rule is unknown names, not this name.
    """
    assert client.post(_PROXY, json=_proxy_body(alias="work")).status_code == 200

    resp = client.post(_PROXY, json=_proxy_body(alias="work") | {"id": 7})

    assert resp.status_code == 422, resp.text
    assert database.get_setting("proxy_alias") == "work"


def test_the_same_save_spelled_correctly_rewrites_the_label(client):
    """Positive control. Without it, "the label survived" would be satisfied by
    a route that could no longer write that field at all: the ground control
    above only shows the FIRST write landing, not that a second one can.
    """
    assert client.post(_PROXY, json=_proxy_body(alias="work")).status_code == 200

    resp = client.post(_PROXY, json=_proxy_body(alias="spare"))

    assert resp.status_code == 200, resp.text
    assert database.get_setting("proxy_alias") == "spare"


@pytest.mark.parametrize("cleared", ["", None, _OMITTED])
def test_clearing_the_label_names_the_proxy_instead_of_deleting_the_name(
        client, cleared):
    """S-9 rewrite. Second positive control, and a regression control only.

    All three inputs used to CLEAR the stored label: the empty string, the
    null the panel sends for an emptied box on a save that also rewrites the
    URL, and the key left out entirely. That was the open question this file
    declined to answer, and it is answered now - an emptied box is not a
    delete, it is a request for the default name. What this still controls is
    that refusing unknown field names did not disturb any of the three: an
    absent name is not an unknown name, so `forbid` never sees the third case.
    """
    assert client.post(_PROXY, json=_proxy_body(alias="work")).status_code == 200

    resp = client.post(_PROXY, json=_proxy_body(alias=cleared))

    assert resp.status_code == 200, resp.text
    assert database.get_setting("proxy_alias") == f"{default_names.PROXY} 1"


def test_both_ends_of_the_feature_refuse_the_same_typo(client):
    """The two routes that write this one label disagreed: the alias-only body
    already answered 422, the combined body answered 200 and deleted it.
    """
    assert client.post(_PROXY, json=_proxy_body(alias="work")).status_code == 200

    combined = client.post(_PROXY, json={
        "proxy_url": _URL, "proxy_required": False, "proxy_allias": "spare",
    })
    alias_only = client.post(_PROXY_ALIAS, json={"proxy_allias": "spare"})

    assert (combined.status_code, alias_only.status_code) == (422, 422)
    assert database.get_setting("proxy_alias") == "work"
