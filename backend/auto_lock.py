"""Lock the vault when nobody is using it.

An unlocked vault is a decrypted vault for as long as the window stays open,
and windows stay open for days. Everything else in this app defends the data
at rest; this is the only thing that shortens the window in which it is not at
rest - a laptop left on a desk, a machine somebody else sits down at, a
session nobody remembered to close.

ON by default, at five minutes. It was off, on the reasoning that a lock which
interrupts somebody mid-conversation is a lock they will turn off and never
turn back on - but that reasoning was about the wrong risk. Idle here already
means "nothing in flight and nothing finished recently", and a streamed reply
holds the clock at zero for as long as it runs, so the interruption the old
default was protecting against cannot happen. What the old default did protect
was a decrypted vault sitting open on an unattended machine, which is the thing
this module exists to prevent. Decided 8 August 2026.

The setting lives in the vault itself, which is the right place: it is only
readable while unlocked, which is exactly when the watchdog needs it.

What it does NOT do is lock while something is happening. Idle means "no
request is in flight and none has finished recently", not "no request has
started recently" - a streamed reply can run for many minutes with nothing
arriving after the first byte, and locking under it would end the reply the
user is sitting there reading.
"""
from __future__ import annotations

import asyncio
import logging

import vault_state

logger = logging.getLogger(__name__)

#: The setting, in minutes. 0 or absent means never.
SETTING = "auto_lock_minutes"

#: How often to look. Coarse on purpose: this wakes for the life of the
#: process, and a lock that arrives up to half a minute late is not a
#: meaningfully worse lock.
TICK_S = 30.0

#: Below this, a "timeout" is an accident - a stray 1 that locks the vault
#: while the user is reading. Values under it are treated as off rather than
#: obeyed, and the settings route refuses them outright.
MIN_MINUTES = 1

#: What a vault that has NEVER been configured gets. An explicit 0 is still
#: off: choosing to leave it open is a choice, and a default may not overrule
#: one. Only the absence of any choice reaches this number.
DEFAULT_MINUTES = 5


def minutes_from_raw(raw: str | None) -> int:
    """What one stored value means, decided in ONE place.

    The watchdog and the settings route both answer this question, and they
    answered it with two separate copies of the same parsing. That was
    survivable while both copies said "absent means off" and stops being
    survivable the moment a default exists: the copies would disagree about a
    vault nobody has configured, and the screen would say "never" while the
    watchdog locked at five minutes. A setting the user is shown and a setting
    the app obeys have to be the same setting.
    """
    if raw in (None, ""):
        return DEFAULT_MINUTES
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        # Never some timeout nobody chose: a corrupt value reads as off.
        logger.warning("Ignoring non-integer %s setting.", SETTING)
        return 0
    return minutes if minutes >= MIN_MINUTES else 0


def configured_minutes() -> int:
    """The user's setting, the default if they have none, or 0 for off.

    Never raises. A locked vault cannot answer this, and that reads as off
    rather than as "lock now" - the vault is already locked, and this is the
    one guard whose failure should be the harmless direction.
    """
    try:
        from database import get_setting

        raw = get_setting(SETTING)
    except Exception:                                    # noqa: BLE001
        return 0
    return minutes_from_raw(raw)


def should_lock() -> bool:
    """Whether the vault is unlocked, configured to lock, and idle enough."""
    if not vault_state.is_unlocked():
        return False
    minutes = configured_minutes()
    if minutes <= 0:
        return False
    return vault_state.idle_seconds() >= minutes * 60


async def lock_now() -> None:
    """The same lock the user gets from the button, from here."""
    from routers.vault import lock_vault_now

    await lock_vault_now(reason="idle")


async def watch(tick: float = TICK_S) -> None:
    """Forever: look, and lock when it is time.

    Never dies on an exception. This task is created once for the process
    lifetime, so an unhandled error would silently disable auto-lock for the
    whole session and the user would go on believing it was on.
    """
    while True:
        try:
            await asyncio.sleep(tick)
            if should_lock():
                logger.info("Auto-lock: idle for %d minute(s), locking",
                            configured_minutes())
                await lock_now()
        except asyncio.CancelledError:
            raise
        except Exception:                                # noqa: BLE001
            logger.warning("Auto-lock check failed", exc_info=True)
