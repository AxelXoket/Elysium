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

import keyring
import keyring.errors

from config import KEYRING_SERVICE

logger = logging.getLogger(__name__)

_MARKER = "legacy-keyring"


def read_legacy(name: str) -> str | None:
    """Value of a legacy keyring entry, or None (absent OR backend broken)."""
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
    try:
        keyring.delete_password(KEYRING_SERVICE, name)
        logger.info("%s: entry %s deleted.", _MARKER, name)
        return True
    except keyring.errors.PasswordDeleteError:
        return True  # already gone - the goal state
    except Exception:
        logger.warning("%s: delete failed for %s (will retry next unlock)", _MARKER, name)
        return False
