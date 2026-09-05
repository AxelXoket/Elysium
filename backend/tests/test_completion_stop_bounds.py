"""The stop list on the completion body, which nothing bounded.

Same defect shape as the settings body the sibling file pins, on a worse
road. `GenerationParams.stop` declared `str | list[str] | None` and no
ceiling, and `_prepare_completion` hands it to
`validate_and_filter_gen_params`, whose walk visits every entry directly on
the event loop with no thread hop. Six routes reach that one call site:
complete, regenerate and edit, each in a plain and a streaming form. So the
cost is not paid by the request that sent the list. It is paid by every reply
already streaming in the process, which stops arriving while the walk runs.

MEASURED with `loop_guard.longest_freeze` wrapped around a real request, put
through `main.app` on the test's own event loop with an httpx ASGITransport so
that reading, parsing and handling the body all run where the meter is
watching. Three runs per row, back to back in one session, and the parse of
the same bytes timed on its own alongside them:

    body                       wire   status   json.loads    longest gap
    no stop list                0 MB     200       0.000s   0.016-0.020s
    at the ceiling              0 MB     200       0.000s   0.015-0.019s
    five million, unbounded    25 MB     200       0.081s   0.136-0.146s
    five million, bounded      25 MB     422       0.086s   0.083-0.099s

Read the last two rows together. Removing the ceiling costs about 0.06s of
held loop, which is pydantic validating the list, `model_dump` copying it and
the walk reading it. What the ceiling does NOT remove is the row's own parse
figure: with the ceiling in place the gap and the parse are the same number,
because the body is read and JSON-parsed before any schema runs. That
remainder is the BYTE dimension and it is not this change's to take.

`loop_guard.MAX_FREEZE_S` is the line those readings are held against.

HOW THE WALK IS COUNTED, because it is the only unusual thing here. The route
is not touched. A stand-in for `validate_and_filter_gen_params` swaps the
`stop` list for one that counts every entry pulled out of it by iteration,
then calls the real function with it, so the production walk really walks and
really returns what it always returned. A count of zero then means the walk
never ran, which a status code alone cannot say: a route that spun through
five million entries and answered 422 afterwards would satisfy the status
line and none of the counts here.

WHY REFUSED RATHER THAN CLAMPED, since the settings route clamps the same
list. There the clamp protects a save, so a stale dialog must not lose the
whole write to a 422. Here nothing is stored and a refusal costs one retry,
there is no clamp on this path to preserve, and dropping entries quietly
would change where the model stops writing. A silent wrong answer is worse
than a loud refusal. The refusal also has to land BEFORE the stream opens, or
it stops being a refusal and becomes a reply that dies halfway; that is why
the six-route test below reads the content type and not only the status.

WHAT THE FREEZE TESTS DO NOT BIND, said here rather than left to be found. A
body at the ceiling and a body with no stop list at all read the same on the
meter, so the freeze pair binds the DIRECTION and not the number: a bounded
list is off the loop, an unbounded one is not. Nothing about the ceiling's own
value comes from a stopwatch. It is pinned instead by
`test_the_ceiling_is_generous_but_not_open_ended`, which states the band as a
rubric against the ceiling that is KEPT, and from below by the sizes in
`test_the_ceiling_never_drops_an_entry`. That band is wide on purpose. It says
the number is sane, never that it is optimal.

WHAT IS NOT BOUNDED, so nobody reads more into these tests than they say. The
ceiling counts ENTRIES. A single stop sequence has no length limit on this
path and the tests below record that it still has none, so a body accepted at
the ceiling can still be enormous, and is still buffered and parsed on the
loop before any schema runs. That is a body-size limit, a different change in
a different place. Nor is the walk moved off the loop: at the ceiling it is
microseconds, and a thread hop costs more than it saves.

Both of those are recorded, not defended, and both would turn something here
red if they were fixed: the walk moving to a worker thread reddens
`test_the_schema_is_the_only_thing_holding_the_loop_open`, and a per-sequence
byte bound reddens `test_one_long_stop_sequence_is_not_measured_in_entries`.
Each of those tests says so in its own docstring. In both cases the red is a
result to read and act on, not a test to silence.
"""
from __future__ import annotations

import json
import time
from typing import get_args

import pytest
from pydantic import ValidationError

import routers.completions as completions
from routers.completions import (
    GenerationParams,
    ProviderPolicy,
    _prepare_completion,
)
from routers.settings import (
    MAX_STOP_SEQUENCES,
    MAX_STOP_SEQUENCES_READ,
    MAX_STOP_SEQUENCE_CHARS,
)

from tests.conftest import make_character, make_chat
from tests.loop_guard import (
    MAX_FREEZE_S,
    STALL_S,
    longest_freeze as _longest_freeze,
)

#: The model id the rest of the suite uses; nothing here dials a provider.
_MODEL_ID = "test/model-1"

#: The other route that takes a stop list, and the one this file is NOT about.
#: It is named here only as the negative case for the route sweep.
_SETTINGS_STOP_SEQUENCES = "/api/v1/settings/stop-sequences"

#: Big enough that the walk is unmistakably a freeze, cheap enough to keep.
#: Measured through `_prepare_completion`, five runs each, longest gap against
#: MAX_FREEZE_S: one million 0.0338s to 0.0344s, two million 0.0571s to
#: 0.0631s, three million 0.0858s to 0.1273s, four million 0.1423s to 0.1681s.
#: Four is the first size clear of the line on a fast run as well as a slow
#: one, which matters because this is the rare assertion a FAST machine breaks.
#:
#: One repeated string, not four million distinct ones. The walk visits the
#: list either way and the timings differ by noise, but the list of pointers
#: costs 30 MiB against 223 MiB, and a MemoryError on a small runner would be a
#: failure this test has nothing to say about.
_UNBOUNDED_ENTRIES = 4_000_000

#: How much larger than the kept ceiling the read ceiling is allowed to be.
#: The read ceiling is meant to be generous, not open-ended, and neither edge
#: of "generous" is pinned by a stopwatch: at these sizes the walk costs
#: nothing a clock can see. So the band is stated as a rubric and enforced
#: against the kept ceiling instead. Below the floor a stale client would be
#: refused, which is what the whole shape exists to avoid; above the roof the
#: word "ceiling" stops meaning anything.
_CEILING_FLOOR = 4
_CEILING_ROOF = 100

#: The six routes that carry a GenerationParams into the walk. Three of them
#: reach it through the extra hop in `_call_provider_for_chat` and three call
#: `_prepare_completion` themselves; three answer with a stream, so a refusal
#: that moved into a handler would show here and nowhere else. The list is
#: written out rather than derived so the paths are readable, and
#: `test_the_route_list_is_the_whole_route_list` is what keeps it honest.
_ROUTES = [
    "/api/v1/chats/{chat_id}/complete",
    "/api/v1/chats/{chat_id}/complete/stream",
    "/api/v1/chats/{chat_id}/messages/{message_id}/regenerate",
    "/api/v1/chats/{chat_id}/messages/{message_id}/regenerate/stream",
    "/api/v1/chats/{chat_id}/messages/{message_id}/edit",
    "/api/v1/chats/{chat_id}/messages/{message_id}/edit/stream",
]


def _entries(count: int) -> list[str]:
    """Distinct, non-empty stop sequences, which is what the walk accepts."""
    return [f"S{n}:" for n in range(count)]


def _body(params: dict) -> dict:
    """One body every route accepts: complete and edit want `message`,
    regenerate ignores it, all three want `model_id`."""
    return {"message": "hello", "model_id": _MODEL_ID,
            "generation_params": params}


def _send(client, chat_id: int, params: dict):
    return client.post(f"/api/v1/chats/{chat_id}/complete", json=_body(params))


def _carries_generation_params(annotation) -> bool:
    """Whether a field's annotation mentions GenerationParams at all.

    `is GenerationParams` alone would miss `GenerationParams | None`, which is
    the shape a seventh route is most likely to be written in, and missing it
    is exactly the gap the sweep below exists to close.
    """
    if annotation is GenerationParams:
        return True
    return any(_carries_generation_params(a) for a in get_args(annotation))


def _refused_by_the_schema(resp) -> bool:
    """Whether the body never reached a handler. An opened stream is not a
    JSON answer, so reading `detail` out of one would raise rather than say
    no."""
    if not resp.headers["content-type"].startswith("application/json"):
        return False
    return resp.json().get("detail") == "validation_failed"


def _prepare(chat_id: int, params: GenerationParams):
    """The coroutine the six routes funnel into, with nothing else varying."""
    return _prepare_completion(
        chat_id, _MODEL_ID, "hello", params, ProviderPolicy(), None, None,
    )


@pytest.fixture()
def chat(client):
    return make_chat(client, make_character(client))


class _CountingList(list):
    """A list that reports every entry iteration pulls out of it."""

    def __init__(self, entries, meter: "_WalkMeter") -> None:
        super().__init__(entries)
        self._meter = meter

    def __iter__(self):
        for entry in list.__iter__(self):
            self._meter.passes += 1
            yield entry


class _WalkMeter:
    """Stands in front of the real validator and counts what its walk reads.

    Calls through, so the filtering the route depends on is the production one
    and the assertions about what reached the provider stay meaningful. The
    one thing it does change is the object identity of the list: what the
    provider receives is the counting list, equal by value to the one sent.
    """

    def __init__(self, real) -> None:
        self._real = real
        self.calls = 0
        self.passes = 0

    def __call__(self, raw: dict) -> dict:
        self.calls += 1
        stop = raw.get("stop")
        if isinstance(stop, list):
            raw = dict(raw, stop=_CountingList(stop, self))
        return self._real(raw)


@pytest.fixture()
def stream_provider(monkeypatch):
    """Fake `openrouter.complete_stream`, recording the params it was given.

    The `provider` fixture in conftest only fakes the non-streaming call, so
    without this the three streaming routes reach a real outbound attempt and
    the egress guard refuses it. That makes them slow and says nothing about
    what was sent.
    """
    import routers.completions as completions_router

    class FakeStream:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def _stream(self, messages, model_id, gen_params, provider, **kwargs):
            self.calls.append({"model_id": model_id, "gen_params": gen_params})

            async def gen():
                yield "ok."

            return gen()

    fake = FakeStream()
    monkeypatch.setattr(completions_router, "complete_stream", fake._stream)
    return fake


@pytest.fixture()
def walk(monkeypatch):
    meter = _WalkMeter(completions.validate_and_filter_gen_params)
    monkeypatch.setattr(completions, "validate_and_filter_gen_params", meter)
    return meter


# -- the body the route will read -------------------------------------------


def test_the_walk_reads_every_entry_it_is_handed(client, provider, chat, walk):
    """Positive control for the absence assertion below, and the trap itself.

    Nothing on this path clamps the list, so the walk visits all of it and the
    whole list rides out to the provider. The counter therefore reads the body
    length whenever a walk happens, which is what makes a later reading of zero
    mean "no walk" rather than "no instrument".
    """
    entries = _entries(MAX_STOP_SEQUENCES_READ)

    resp = _send(client, chat, {"stop": entries})

    assert resp.status_code == 200, resp.text
    assert walk.passes == MAX_STOP_SEQUENCES_READ
    assert provider.calls[-1]["gen_params"]["stop"] == entries


def test_a_body_over_the_ceiling_never_reaches_the_walk(
    client, provider, chat, walk,
):
    """The refusal has to happen before the walk, not inside it.

    A status code alone would not say that: a route that walked four million
    entries and then answered 422 would pass the status line. The counts are
    what separate the two, and the test above is what proves they can see a
    walk at all.
    """
    kept = _send(client, chat, {"stop": ["Human:"]})
    # Ground: the route is reachable, the instrument is armed, and a request
    # of this shape does reach the provider.
    assert kept.status_code == 200, kept.text
    assert (walk.calls, walk.passes) == (1, 1)
    assert len(provider.calls) == 1
    walk.calls = walk.passes = 0

    resp = _send(client, chat, {"stop": _entries(MAX_STOP_SEQUENCES_READ + 1)})

    # The walk first, because it is the claim; the provider count is the second
    # damage; the status code is only how the caller finds out.
    assert (walk.calls, walk.passes) == (0, 0), (
        f"the validator ran {walk.calls} time(s) "
        f"and read {walk.passes} entries")
    assert len(provider.calls) == 1, "a refused body reached the provider"
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "validation_failed"


def test_the_route_list_is_the_whole_route_list():
    """The list above is written by hand, so something has to catch a seventh.

    Asked of the running app rather than of the source: every mounted route
    whose request body carries a `GenerationParams` reaches the same walk, so
    the set of such routes and the set tested here have to be the same set.
    A new endpoint taking generation settings lands in this assertion, not in
    a silent gap.
    """
    from fastapi.routing import APIRoute
    import main

    carrying, with_a_body = set(), set()
    for route in main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        body = getattr(route, "body_field", None)
        if body is None:
            continue
        with_a_body.add(route.path)
        model = getattr(body.field_info, "annotation", None)
        fields = getattr(model, "model_fields", {})
        if any(_carries_generation_params(f.annotation)
               for f in fields.values()):
            carrying.add(route.path)

    # The instrument first: it has to see the optional shape as well as the
    # bare one, and it has to say no to something unrelated.
    assert _carries_generation_params(GenerationParams)
    assert _carries_generation_params(GenerationParams | None)
    assert not _carries_generation_params(str)

    # Ground, then discrimination. The sweep finds body-taking routes at
    # large, and the filter keeps only those carrying generation params: the
    # settings route below takes a body and a stop list of its own and must
    # NOT be swept in, or the comparison would be about having a body rather
    # than about reaching the walk.
    assert _SETTINGS_STOP_SEQUENCES in with_a_body
    assert _SETTINGS_STOP_SEQUENCES not in carrying
    assert carrying == set(_ROUTES)


def test_the_ceiling_is_generous_but_not_open_ended():
    """The one assertion that pins the ceiling's own value from above.

    The freeze tests cannot: at these sizes the walk costs nothing measurable,
    so the ceiling could float into the millions with every timing assertion
    still green. This states the band instead, against the ceiling that is
    actually kept, and it is deliberately wide - it is a sanity rail, not a
    claim that the number is optimal.
    """
    floor = MAX_STOP_SEQUENCES * _CEILING_FLOOR
    roof = MAX_STOP_SEQUENCES * _CEILING_ROOF
    assert floor < roof, "the rubric collapsed and pins nothing"

    # Ground: the floor is admitted, so the refusal below is about the roof
    # and not about the schema having stopped accepting lists.
    assert len(GenerationParams(stop=_entries(floor)).stop) == floor

    with pytest.raises(ValidationError):
        GenerationParams(stop=_entries(roof))


@pytest.mark.parametrize("route", _ROUTES)
def test_every_route_refuses_before_it_opens_a_stream(
    client, chat, provider, stream_provider, route: str,
):
    """The refusal is the schema's, so it is the same on all six roads.

    The content type is the load-bearing half. Three of these routes answer
    with `text/event-stream`, and a refusal that had moved into the handler
    would open that stream and put the failure in a frame inside it. A caller
    reading only the status could not tell the two apart, and neither could
    any other test in this file, which drives one route.

    Ground control is in the second half: the same route, the same body, one
    entry fewer, is not refused for this reason. Without it a typo in the path
    would answer 404, and "not validation_failed" would pass on a route that
    does not exist. That half deliberately asserts nothing about how the
    request then FARES: four of these routes go on to refuse it for a reason
    of their own, message id 1 being neither regenerable nor editable here,
    and being refused for a different reason is itself proof the schema let
    the body through.
    """
    url = route.format(chat_id=chat, message_id=1)

    over = client.post(
        url, json=_body({"stop": _entries(MAX_STOP_SEQUENCES_READ + 1)}))

    # The content type first, because it is the claim: a stream that opened is
    # a reply that dies, not a refusal. The status code is second.
    assert over.headers["content-type"].startswith("application/json"), (
        "the refusal arrived inside an opened stream")
    assert over.status_code == 422, over.text
    assert over.json()["detail"] == "validation_failed"
    # Positive control for the helper used under a `not` below: without this
    # a helper that always answered False would satisfy the ground control.
    assert _refused_by_the_schema(over)

    at_ceiling = client.post(
        url, json=_body({"stop": _entries(MAX_STOP_SEQUENCES_READ)}))

    assert at_ceiling.status_code != 404, "the route was never reached"
    assert not _refused_by_the_schema(at_ceiling), at_ceiling.text


@pytest.mark.parametrize("size", [
    1,
    MAX_STOP_SEQUENCES,
    MAX_STOP_SEQUENCES * _CEILING_FLOOR,
    MAX_STOP_SEQUENCES_READ,
])
def test_the_ceiling_never_drops_an_entry(client, provider, chat, size: int):
    """Ground control for the whole unit: the ceiling must not become a clamp.

    The settings route clamps its list because losing a save is worse than
    keeping four of five entries. Here the list decides where the model stops
    writing, so anything the schema admits is forwarded whole. Every size a
    caller can actually produce lives under this rule, the dialog's own four
    among them.
    """
    entries = _entries(size)

    resp = _send(client, chat, {"stop": entries})

    assert resp.status_code == 200, resp.text
    assert provider.calls[-1]["gen_params"]["stop"] == entries


def test_the_list_reaches_the_provider_on_a_streaming_route_too(
    client, stream_provider, chat,
):
    """The other funnel. Every other forwarding assertion here goes through
    the non-streaming route, which reaches the walk via an extra hop; this one
    takes the road that calls `_prepare_completion` itself. Between the two,
    both shapes are shown carrying the list out rather than only refusing it.
    """
    entries = _entries(MAX_STOP_SEQUENCES)

    with client.stream(
        "POST", f"/api/v1/chats/{chat}/complete/stream",
        json=_body({"stop": entries}),
    ) as resp:
        assert resp.status_code == 200
        resp.read()

    assert stream_provider.calls[-1]["gen_params"]["stop"] == entries


@pytest.mark.parametrize("length", [
    MAX_STOP_SEQUENCES_READ + 1,
    MAX_STOP_SEQUENCE_CHARS,
    1_000_000,
])
def test_one_long_stop_sequence_is_not_measured_in_entries(
    client, provider, chat, length: int,
):
    """The ceiling counts entries in a list, never characters in a string.

    A single sequence is allowed more characters than the list is allowed
    entries, so a ceiling written onto the field rather than onto the list arm
    would refuse a sequence the settings dialog can save. That is the mistake
    this test exists to catch. The first length is derived from the entry
    ceiling and so can never stop discriminating, whatever either ceiling is
    set to later; the second is the longest the dialog will send; the third is
    far past both, so a length cap introduced anywhere between them lands here.
    Nothing here is asserted against `MAX_STOP_SEQUENCE_CHARS`, deliberately:
    tying this to the dialog's character limit would turn the file red the day
    somebody raises the number of stop sequences the dialog holds, which is a
    change this path has no opinion about.

    NOTE for whoever bounds the BYTE dimension, since that work would meet
    these three cases red. The absence of a per-sequence limit is recorded
    here as today's behaviour, not defended as correct. The file says at the
    top that the byte dimension is unbounded and out of scope; a change that
    bounds it should edit these lengths, not work around them.
    """
    long_one = "H" * length

    resp = _send(client, chat, {"stop": long_one})

    assert resp.status_code == 200, resp.text
    assert provider.calls[-1]["gen_params"]["stop"] == long_one


# -- and the loop the walk was running on -----------------------------------


@pytest.mark.anyio
async def test_the_worst_count_the_schema_admits_stays_off_the_loop(
    anyio_backend, client, chat,
):
    """Worst case by count, through the coroutine all six routes funnel into.

    Built through the schema rather than around it, so the second half is the
    same statement about count that the route relies on: nothing with more
    entries can be built at all.

    Read the name literally, and read the docstring at the top of this file
    before trusting the margin. This binds the COUNT dimension and binds it
    very loosely; the byte dimension is not bounded by the ceiling and is not
    measured here.
    """
    entries = _entries(MAX_STOP_SEQUENCES_READ)
    params = GenerationParams(stop=entries)

    freeze, prepared = await _longest_freeze(_prepare(chat, params))

    assert prepared[1]["stop"] == entries, "the walk did not actually run"
    assert freeze < MAX_FREEZE_S

    with pytest.raises(ValidationError):
        GenerationParams(stop=_entries(MAX_STOP_SEQUENCES_READ + 1))


@pytest.mark.anyio
async def test_the_schema_is_the_only_thing_holding_the_loop_open(
    anyio_backend, client, chat,
):
    """Positive control for the absence assertion above, on the same coroutine.

    Nothing here changes except the length of the list, and the list is built
    around the schema because the schema is now the only thing that refuses it.
    Without this the test above would prove the ceiling safe whatever it was
    set to, since a meter that always read zero would satisfy it.

    NOTE for whoever moves the walk off the event loop, since that work would
    meet this test red. The walk being on the loop is recorded here as today's
    behaviour, not defended as correct: at this ceiling a thread hop costs more
    than it saves, which is a judgement about the size, not about the shape. A
    change that moves it should edit this test, not work around it.
    """
    params = GenerationParams.model_construct(
        stop=["A"] * _UNBOUNDED_ENTRIES)

    freeze, prepared = await _longest_freeze(_prepare(chat, params))

    assert len(prepared[1]["stop"]) == _UNBOUNDED_ENTRIES
    assert freeze >= MAX_FREEZE_S


@pytest.mark.anyio
async def test_the_freeze_meter_reports_a_freeze_when_there_is_one(
    anyio_backend,
):
    """Second positive control, with the production path taken out of it, so a
    red above can be read as the coroutine's fault rather than the meter's.

    This is the same assertion `test_settings_body_bounds.py` makes about the
    same helper, and it is duplicated on purpose rather than shared: a red
    here has to be readable without opening another file. If a third file
    wants it, the honest move is one test next to `loop_guard.py` and none in
    any of the three.
    """

    async def blocks_the_loop():
        time.sleep(STALL_S)
        return None

    freeze, _ = await _longest_freeze(blocks_the_loop())

    assert freeze >= MAX_FREEZE_S


# -- the walk is still the walk ---------------------------------------------


def test_an_empty_entry_is_still_refused_under_the_ceiling(
    client, provider, chat,
):
    """The ceiling is an addition, not a replacement.

    The walk refuses an empty entry with the app's own error code rather than
    a validation array, and that branch still has to fire for a list the schema
    now admits.

    Its two neighbours in the same walk are NOT tested here, and the reason is
    the same for both: the schema gets there first. A non-string ENTRY is
    refused by `list[str]`, and a `stop` that is neither a string nor a list at
    all is refused by the union, both before the walk sees anything. Measured
    against the schema: 5, 5.5, True, {} and [1, 2] each answer 422 without
    reaching this call. Testing either would be testing pydantic.
    """
    ground = _send(client, chat, {"stop": ["Human:"]})
    # Ground: this chat and this body shape do reach the provider, so an empty
    # list of calls below means refused, not merely unreachable.
    assert ground.status_code == 200, ground.text
    assert len(provider.calls) == 1

    full = _entries(MAX_STOP_SEQUENCES_READ - 1) + [""]
    resp = _send(client, chat, {"stop": full})

    assert len(provider.calls) == 1, "a refused body reached the provider"
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "invalid_gen_params"


def test_an_empty_list_is_dropped_rather_than_forwarded(
    client, provider, chat,
):
    """`stop: []` is the box cleared, and the walk drops it before the payload
    is built. The ceiling did not touch that branch and the provider must not
    start receiving an empty list it would have to interpret.
    """
    ground = _send(client, chat, {"stop": ["Human:"]})
    # Ground: the recorder does carry `stop` when one was sent.
    assert ground.status_code == 200, ground.text
    assert provider.calls[-1]["gen_params"]["stop"] == ["Human:"]

    resp = _send(client, chat, {"stop": []})

    assert "stop" not in provider.calls[-1]["gen_params"], resp.text
    assert resp.status_code == 200, resp.text


def test_the_refusal_does_not_echo_the_list_back(client, chat):
    """A 422 that quoted the body would hand back everything it just refused.

    The narrowed validation handler is what stops that, and this is the first
    field on this path able to make pydantic hold a very large value at the
    moment it builds an error, so the shape is pinned here rather than assumed.
    The answer is also compared against the request rather than against a
    number, so it stays a statement about echoing and not about byte counts.
    """
    marker = "ZzStopMarkerZz"
    entries = [f"{marker}{n}:" for n in range(MAX_STOP_SEQUENCES_READ + 1)]
    body = _body({"stop": entries})
    sent = json.dumps(body)
    # Ground: the marker really is in what was sent, so its absence below is
    # about the answer rather than about a marker that never existed.
    assert marker in sent

    resp = client.post(f"/api/v1/chats/{chat}/complete", json=body)

    assert marker not in resp.text, "the refusal quoted the list back"
    assert len(resp.text) < len(sent), resp.text[:200]
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "validation_failed"


def test_the_ceiling_did_not_turn_a_missing_stop_into_an_empty_one(
    client, provider, chat,
):
    """Regression control: `stop` is optional and its absence must stay
    absent, not arrive at the provider as an empty list the model would have
    to be told about.
    """
    ground = _send(client, chat, {"stop": ["Human:"], "temperature": 0.5})
    # Ground: the provider record really does carry `stop` when one was sent,
    # so its absence below is the route's doing and not the recorder's.
    assert ground.status_code == 200, ground.text
    assert provider.calls[-1]["gen_params"]["stop"] == ["Human:"]

    resp = _send(client, chat, {"temperature": 0.5})

    assert "stop" not in provider.calls[-1]["gen_params"], resp.text
    assert resp.status_code == 200, resp.text
