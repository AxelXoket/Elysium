"""keyring_service.py - LEGACY OS-keyring access, migration-only (E5).

App secrets moved into the encrypted vault DB (secrets_service.py). This
module remains solely so the one-time unlock migration can read the old
Credential Manager entries and delete them after a verified copy. Nothing
else may import it; new code uses secrets_service.

Every function is failure-tolerant by design: a broken/absent keyring
backend must never block the app. Failures log a DISTINCT marker (no
values) so a packaged install with a broken keyring is visible in
elysium.log instead of silently keeping the legacy entry around.
"""

import logging
import os

import keyring
import keyring.errors

from config import KEYRING_SERVICE

logger = logging.getLogger(__name__)

_MARKER = "legacy-keyring"

#: Set to "1" by throwaway instances - tests, E2E runs, a second copy started
#: to reproduce something. The credential store is MACHINE-GLOBAL: it does not
#: care that this process has its own vault, its own database and its own
#: temporary folder, so without this switch a disposable instance deletes the
#: developer's real saved key from the real Windows store.
#:
#: THE CHECK BELONGS HERE, at the only place that touches that store. It used
#: to live in legacy_migration alone, which covered the unlock migration and
#: nothing else - so a throwaway instance that merely SAVED a key through
#: Settings still sent a real delete_password into the machine-wide store,
#: through a route that never asked. Four settings routes reached the store
#: behind the switch's back. A guard on the door is worth more than a guard on
#: one of the corridors leading to it.
SKIP_ENV = "ELYSIUM_SKIP_LEGACY_MIGRATION"


def disabled() -> bool:
    """Read on every call, never cached: tests set and unset this per case."""
    return os.environ.get(SKIP_ENV) == "1"


def read_legacy(name: str) -> str | None:
    """Value of a legacy keyring entry, or None (absent OR backend broken)."""
    if disabled():
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        logger.warning("%s: read failed for %s (backend unavailable?)", _MARKER, name)
        return None


#: Written when a legacy entry could not be deleted, so the unlock migration
#: knows the user revoked it rather than never having touched it. Without this
#: the two states are identical from the vault's side - "no row" - and the
#: migration reads a revoked key as one that still needs importing.
REVOKED_PREFIX = "legacy_revoked:"


def revoked_key(name: str) -> str:
    return REVOKED_PREFIX + name


def delete_legacy(name: str) -> bool:
    """Delete a legacy keyring entry. True on success or already-absent."""
    if disabled():
        # True, not False, and the choice is about COHERENCE. With the switch
        # on, read_legacy already answers "there is nothing here"; a delete
        # that answered "a copy is still out there" would describe a different
        # world than the reads do, and _revoke_legacy would write a tombstone
        # for an entry this process was never allowed to look at. The switch
        # presents one consistent fiction: an empty legacy store.
        #
        # Logged rather than silent, because the same switch left on in a real
        # install would let a real legacy copy survive while the app reports it
        # gone - and that sentence should be findable in elysium.log.
        logger.warning("%s: %s is set, so the delete of %s was not attempted.",
                       _MARKER, SKIP_ENV, name)
        return True
    try:
        keyring.delete_password(KEYRING_SERVICE, name)
        logger.info("%s: entry %s deleted.", _MARKER, name)
        return True
    except keyring.errors.PasswordDeleteError:
        return True  # already gone - the goal state
    except Exception:
        logger.warning("%s: delete failed for %s (will retry next unlock)", _MARKER, name)
        return False
