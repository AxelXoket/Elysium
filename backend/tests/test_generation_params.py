"""The sampling dials, checked where a client can actually turn them.

`GenerationParams` and the range check behind it were reachable from every
send, and no test in the suite imported either of them. The only thing
exercising the edges was verify/verify_phase5b.py, which crashes long before
it gets there - so a validator could have been deleted outright and every gate
would still have been green.

Two layers, and they fail differently on purpose:

  * the pydantic model refuses values that are not numbers at all (a string, a
    bool, 1.9 where an integer is required) with a 422 from FastAPI;
  * validate_and_filter_gen_params refuses numbers that are outside the range
    the provider accepts, which the router turns into a 422 with a stable
    code.

Both are tested through the endpoint rather than by calling the model, because
"a client sent this" is the situation that matters and the wiring between the
two layers is exactly what an isolated unit test would skip.
"""
from __future__ import annotations

import pytest

from openrouter import validate_and_filter_gen_params
from conftest import make_character, make_chat


def _send(client, chat_id: int, params: dict):
    return client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "hello",
        "model_id": "test/model-1",
        "generation_params": params,
    })


@pytest.fixture()
def chat(client):
    return make_chat(client, make_character(client))


# ---------------------------------------------------------------------------
# Not a number at all
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", [
    "temperature", "top_p", "top_k", "min_p", "top_a",
    "frequency_penalty", "presence_penalty", "repetition_penalty",
    "max_tokens", "seed",
])
@pytest.mark.parametrize("value", ["0.7", True, False])
def test_a_dial_that_is_not_a_number_is_refused(client, chat, provider,
                                                field: str, value):
    """Strings and bools, on every numeric field.

    bool is the interesting one: it is an int subclass in Python, so
    `temperature=True` sails through an isinstance(v, (int, float)) check and
    reaches the provider as 1. The check has to name bool before it names int,
    and nothing was proving it still did.
    """
    resp = _send(client, chat, {field: value})

    assert resp.status_code == 422, (field, value, resp.text)
    assert provider.calls == [], "a rejected request still reached the provider"


@pytest.mark.parametrize("field", ["top_k", "max_tokens", "seed"])
def test_a_fractional_value_for_an_integer_dial_is_refused(
    client, chat, provider, field: str,
):
    resp = _send(client, chat, {field: 1.9})

    assert resp.status_code == 422, field
    assert provider.calls == []


@pytest.mark.parametrize("field", ["top_k", "max_tokens", "seed"])
def test_a_whole_number_written_as_a_float_is_accepted(
    client, chat, provider, field: str,
):
    """JSON has one number type, so a client that sends 40 may send 40.0. That
    is the same value, and refusing it would be refusing valid JSON."""
    resp = _send(client, chat, {field: 40.0})

    assert resp.status_code == 200, (field, resp.text)


# ---------------------------------------------------------------------------
# A number, but not one the provider will take
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("temperature", 2.1),
    ("temperature", -0.1),
    ("top_p", 1.1),
    ("top_p", -0.5),
    ("min_p", 1.5),
    ("top_a", 2.0),
    ("frequency_penalty", 2.5),
    ("frequency_penalty", -2.5),
    ("presence_penalty", 3.0),
    ("max_tokens", 0),
    ("top_k", -1),
    # These two were in the "not a number" and "fractional" lists but in
    # neither range list, so their bounds were the only ones in _PARAM_SPEC
    # that nothing checked. repetition_penalty's floor is 0.001 rather than
    # 0.0 - a divide-by-something guard, easy to widen by accident - and
    # seed's ceiling is the signed 32 bit limit, which is exactly the sort of
    # number a copy-paste turns into the unsigned one.
    ("repetition_penalty", 2.5),
    ("repetition_penalty", 0.0),
    ("seed", 2 ** 31),
    ("seed", -(2 ** 31) - 1),
])
def test_a_dial_outside_its_range_is_refused(client, chat, provider,
                                             field: str, value):
    resp = _send(client, chat, {field: value})

    assert resp.status_code == 422, (field, value, resp.text)
    assert provider.calls == []


@pytest.mark.parametrize("field,value", [
    ("temperature", 0.0),
    ("temperature", 2.0),
    ("top_p", 1.0),
    ("min_p", 0.0),
    ("frequency_penalty", -2.0),
    ("presence_penalty", 2.0),
    ("max_tokens", 1),
    ("top_k", 0),
    ("repetition_penalty", 0.001),
    ("repetition_penalty", 2.0),
    ("seed", 2 ** 31 - 1),
    ("top_a", 1.0),
    ("min_p", 1.0),
])
def test_the_ends_of_each_range_are_inside_it(client, chat, provider,
                                              field: str, value):
    """Guard the guard. A range check written with the wrong comparison
    refuses the values a user is most likely to pick deliberately, and every
    "is it refused" test above would still pass."""
    resp = _send(client, chat, {field: value})

    assert resp.status_code == 200, (field, value, resp.text)
    assert provider.calls[-1]["gen_params"][field] == value


# ---------------------------------------------------------------------------
# What reaches the provider
# ---------------------------------------------------------------------------

def test_an_unknown_dial_is_dropped_rather_than_forwarded(client, chat,
                                                          provider):
    """extra="ignore" on the model. A client inventing a parameter must not be
    able to put an arbitrary key into the body this app sends upstream."""
    resp = _send(client, chat, {"temperature": 0.5, "made_up_knob": "whatever"})

    assert resp.status_code == 200
    assert "made_up_knob" not in provider.calls[-1]["gen_params"]


def test_a_dial_left_out_is_not_sent_as_null(client, chat, provider):
    """Absent means "provider default", and None is not that: a literal null
    in the payload is a value, and some providers reject it."""
    resp = _send(client, chat, {"temperature": 0.5})

    assert resp.status_code == 200
    sent = provider.calls[-1]["gen_params"]
    assert sent == {"temperature": 0.5}
    assert not any(v is None for v in sent.values())


def test_the_validator_keeps_nothing_it_does_not_understand():
    """The allow-list, directly. It is what stands between a request body and
    the JSON this app posts to a third party."""
    kept = validate_and_filter_gen_params({
        "temperature": 0.5,
        "stream": True,             # decided here, never by a caller
        "model": "someone/else",    # ditto
        "provider": {"zdr": False},  # the privacy lock, in disguise
    })

    assert kept == {"temperature": 0.5}
