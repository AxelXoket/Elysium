"""routers/activity.py - the activity ping the frontend sends for real input.

Route (under /api/v1; NOT under /vault; NOT in main._IDLE_EXEMPT):
    POST /activity -> 204, no body

The frontend's activity tracker (frontend/src/lib/activity/
installActivityTracker.ts) sends this at most once every PING_MIN_GAP_S
seconds, and only when a real input event happened since the last ping: a
click, a key, a wheel, a touch, focus arriving, or pointer movement over the
focused, visible window past a distance threshold. It carries nothing and
answers nothing, which is why it is a 204 rather than {"ok": true}: the
frontend never reads the answer, so a body would only be weight on a request
sent twice a minute.

The handler is empty on purpose, and that emptiness is measured
(tests/test_activity_ping.py):

- The idle clock is stamped by the vault_gate middleware in main.py, which
  calls vault_state.enter_request() before any handler runs. This route
  moves the clock because it is an ordinary /api/v1 request that is not in
  the exempt set, and for no other reason.
- vault_state.touch() is NOT called here. It would be a second path to the
  same clock, and a subset of what enter_request() already does. The test
  wraps touch() and asserts zero calls while the clock still resets.
- There is no server-side rate limit in this body, because a limit here
  would limit nothing: the clock is already stamped by the time the body
  runs. If a limit is ever wanted its place is vault_gate itself, for the
  whole of /api/v1, and that is a separate decision.
- While the vault is locked the gate answers 423 before the stamp, so a
  locked vault's clock is never moved by this route. That is the reason the
  route lives outside /vault: everything under that prefix passes the gate
  while locked.
"""
from __future__ import annotations

from fastapi import APIRouter, Response

router = APIRouter(tags=["activity"])


@router.post("/activity", status_code=204, response_class=Response)
async def activity_ping() -> Response:
    """Nothing to do: the middleware has already recorded the activity."""
    return Response(status_code=204)
