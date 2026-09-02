"""loop_guard.py - the two ways this suite proves a handler left the loop alone.

Not a test module (no `test_` prefix, so pytest does not collect it). It holds
what nine `*_loop_blocking.py` files had each copy-pasted: two measuring
helpers and the stall constant they share.

KADEME 20a wrote this. What it did NOT do is fold those nine files into one
parameterised file, which is what the plan asked for - the reason is recorded
in TEST-PLAN-DOGRULAMA.md and comes down to this: the files differ in what
they measure and each one carries the story of a different production bug.
Only the measuring instruments were ever really duplicated, so only the
measuring instruments moved.

WHICH ONE TO USE - this is the part that was buried in one file's docstring
and is the reason both exist:

  `ticks_during` counts how often the loop got control. Use it when the
  handler under test does ONE piece of blocking work. It answers "did the
  loop run at all while this ran".

  `longest_freeze` returns the longest unbroken stretch the loop went without
  control. Use it when the handler does SEVERAL pieces of database work and
  some of them already run on a worker thread. Those threaded pieces keep the
  loop ticking no matter what the piece under test does, so a "did it tick at
  all" assertion passes either way. The longest gap is the honest measure: if
  any single call blocked the loop, exactly one gap grows to the length of the
  injected stall, and nothing else in the handler can produce one.

A third proof lives in test_open_speaker_off_loop.py and did NOT move here: a
`threading.Event` gate set from outside the coroutine under test. It proves
something neither helper can - that the loop stayed free for work nobody in
this measurement scheduled. Both helpers instrument a heartbeat they start
themselves; the gate proves liveness to a stranger.

THE THRESHOLDS ARE MEASURED, NOT GUESSED. KADEME 20a ran a 0.12s unit of work
on this machine with a varying share of it held on the loop, five runs
per row:

    share on the loop | ticks (min of 5) | longest gap
    ------------------+------------------+------------
     0%  (correct)    |        8         |   0.0162
     25%              |        7         |   0.0442
     50%              |        5         |   0.0747
     75%              |        3         |   0.1059
     100%             |        1         |   0.1359

Read that table before changing anything here, because it says plainly what
each instrument can and cannot see.

The old assertions were `ticks > 1` and `freeze < STALL_S`. Against the table,
BOTH caught only the 100% row - `> 1` passes a 75%-blocked handler (3 ticks),
and `< 0.12` passes 0.1059. So the suite could tell a total freeze from a
clean run and nothing in between.

`MAX_FREEZE_S = 0.05` is the real gain: it fails the 50%, 75% and 100% rows
and sits 3.1x above a correct run's worst gap. That is a sharp instrument.

`MIN_TICKS` is the blunt one, and it is worth being honest about why. To fail
the 50% row it would have to be 6, against a correct-run minimum of 8 - a
1.3x margin, which a loaded machine would break. At 4 it fails 100% always and
75% about half the time, with a 2x margin. That is better than `> 1` and it is
not much better. The first version of this file set it to 3, which the table
shows would have changed nothing at all; the measurement caught that, not the
reasoning.

The conclusion for anyone adding a test: prefer `longest_freeze`. The tick
count is kept because five files already read well with it and because it
answers a different question, not because it is the better guard.

A test that cries wolf gets deleted, so neither constant is set to the
sharpest value the table would allow.

Note on granularity: `asyncio.sleep(0.005)` actually returns after about
0.0158s here, because Windows' default timer resolution is ~15.6ms. That is
why a 0.12s stall yields eight ticks rather than twenty-four, and why MIN_TICKS
is not simply STALL_S / TICK_S.
"""

from __future__ import annotations

import asyncio
import time

#: Long enough that a blocked loop is unambiguous, short enough to stay cheap.
STALL_S = 0.12

#: The heartbeat period. See the granularity note above before trusting it.
TICK_S = 0.005

#: Floor for `ticks_during`. Correct run gives 8; a fully blocked one gives 1.
#: See the table above for what this does and does not catch.
MIN_TICKS = 4

#: Ceiling for `longest_freeze`. Correct run's worst gap is 0.0162s; a fully
#: blocked one is 0.1359s. Fails every row from 50% blocking upwards.
MAX_FREEZE_S = 0.05


async def ticks_during(coro) -> tuple[int, object]:
    """Run `coro`, counting how many times the loop got control meanwhile."""
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(TICK_S)
            ticks += 1

    beat = asyncio.ensure_future(heartbeat())
    try:
        await asyncio.sleep(0.02)          # let the heartbeat settle
        before = ticks
        result = await coro
        return ticks - before, result
    finally:
        beat.cancel()


async def longest_freeze(coro) -> tuple[float, object]:
    """Run `coro`, returning the longest stretch the loop went without control."""
    gaps: list[float] = []

    async def heartbeat():
        last = time.monotonic()
        while True:
            await asyncio.sleep(TICK_S)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    beat = asyncio.ensure_future(heartbeat())
    try:
        await asyncio.sleep(0.02)          # let the heartbeat settle
        gaps.clear()
        result = await coro
        # Yield once BEFORE reading the gaps. A handler whose blocking call is
        # its last act leaves the loop starved right up to the moment it
        # returns, and the heartbeat has had no chance to record that stretch
        # yet - measuring here without this sleep reported a clean run for a
        # fully blocked one, and made the whole assertion unfalsifiable.
        await asyncio.sleep(TICK_S)
        return (max(gaps) if gaps else 0.0), result
    finally:
        beat.cancel()
