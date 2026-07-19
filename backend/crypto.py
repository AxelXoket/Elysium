"""crypto.py - passphrase → key derivation and vault identity files.

Ported from Wisteria's proven model (backend/memory/crypto.py) with
Elysium-specific domain constants. Decision record (docs/ENCRYPTION_PLAN.md):
v1 has NO "remember on this device" - every launch asks for the passphrase,
so no DPAPI code and no device.key exists here by design.

The 256-bit DB key is derived from the passphrase with scrypt (stdlib,
memory-hard) and is NEVER stored - the DB cannot be opened without the
passphrase, not even inside the user's own Windows session. Forgetting the
passphrase means the data is unrecoverable (by design).

Persisted beside the DB (neither is secret):
  - salt.bin     : scrypt salt
  - verifier.bin : HMAC(key, domain) - distinguishes "wrong passphrase" from
                   "corrupt file" WITHOUT storing the key; knowing it does
                   not reveal the key.

Passphrases are never logged anywhere in this module.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from pathlib import Path

# ---------------------------------------------------------------- scrypt KDF

# Memory-hard params: N=2^15 (~32 MB), r=8, p=1 - fast for one unlock,
# painful to brute-force. Runs ONCE per unlock; the engine gets the raw key
# (PRAGMA key = "x'…'"), so per-connection KDF cost is zero.
_SCRYPT = dict(n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)


def new_salt() -> bytes:
    return secrets.token_bytes(16)


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive the 32-byte DB key from the passphrase (never stored)."""
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, **_SCRYPT)


# v1 protocol domain constant. BYTE-STABLE FOREVER: existing vaults derive
# their verifier from exactly these bytes - changing them locks users out.
_VERIFY_DOMAIN = b"elysium-vault-verify-v1"


def _verifier(key: bytes) -> bytes:
    return hmac.new(key, _VERIFY_DOMAIN, hashlib.sha256).digest()


def make_verifier(key: bytes) -> bytes:
    return _verifier(key)


def check_verifier(key: bytes, stored: bytes) -> bool:
    return hmac.compare_digest(_verifier(key), stored)


# ---------------------------------------------------------------- vault files

class KeyVault:
    """Manages the passphrase-derived key's identity files (salt + verifier).

    The ultimate authority on key correctness is the encrypted DB itself;
    the verifier is a convenience (fast wrong-passphrase feedback). The
    recovery paths below repair identity files from a DB-validated key.
    """

    def __init__(self, dir_path: Path) -> None:
        self.dir = Path(dir_path)
        self.salt_path = self.dir / "salt.bin"
        self.verifier_path = self.dir / "verifier.bin"

    # -- state ---------------------------------------------------------------
    def is_initialized(self) -> bool:
        """True once a passphrase has been set (salt + verifier exist)."""
        return self.salt_path.exists() and self.verifier_path.exists()

    def can_derive(self) -> bool:
        """Salt still present - a passphrase key can still be derived."""
        return self.salt_path.exists()

    # -- first run: set the passphrase ---------------------------------------
    def initialize(self, passphrase: str) -> bytes:
        """FIRST setup only - the caller guarantees no encrypted DB exists
        for a different key (overwriting the salt of a live vault = permanent
        loss). As a safety net, existing identity files are shelved, never
        deleted."""
        self.dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        for p in (self.salt_path, self.verifier_path):
            if p.exists():
                try:
                    p.replace(p.with_name(f"{p.name}.bak-{ts}"))
                except OSError:
                    pass
        salt = new_salt()
        key = derive_key(passphrase, salt)
        self.salt_path.write_bytes(salt)
        self.verifier_path.write_bytes(make_verifier(key))
        return key

    # -- later runs: unlock ---------------------------------------------------
    def unlock(self, passphrase: str) -> bytes | None:
        """Return the key if the passphrase is correct, else None."""
        if not self.is_initialized():
            return None
        salt = self.salt_path.read_bytes()
        key = derive_key(passphrase, salt)
        if check_verifier(key, self.verifier_path.read_bytes()):
            return key
        return None

    # -- DB-validated recovery ------------------------------------------------
    # Principle: the encrypted DB is the source of truth for key correctness;
    # salt/verifier are conveniences. Verifier loss / corruption / a
    # half-finished passphrase change are repairable while the DB opens.

    def heal(self, key: bytes) -> None:
        """Rewrite the verifier for a DB-validated key (salt untouched;
        the verifier is HMAC(key) and salt-independent)."""
        self.verifier_path.write_bytes(make_verifier(key))

    def recover_with_db(self, passphrase: str, db_check) -> bytes | None:
        """Try candidate salts (current + a half-finished .new); for the first
        key that db_check(key) validates, make the identity files consistent
        and return the key. Returns None (files untouched) if none opens."""
        salt_new = self.salt_path.with_name("salt.bin.new")
        ver_new = self.verifier_path.with_name("verifier.bin.new")
        for sp in (self.salt_path, salt_new):
            if not sp.exists():
                continue
            try:
                salt = sp.read_bytes()
                key = derive_key(passphrase, salt)
            except Exception:
                continue
            if not db_check(key):
                continue
            try:  # best-effort repair: a write error must not block entry
                if sp != self.salt_path:
                    self.salt_path.write_bytes(salt)
                self.verifier_path.write_bytes(make_verifier(key))
                for leftover in (salt_new, ver_new):
                    if leftover.exists():
                        leftover.unlink()
            except OSError:
                pass
            return key
        return None

    # -- change passphrase -----------------------------------------------------
    def change_passphrase(self, new_passphrase: str, rekey_fn, verify_fn) -> bytes:
        """Crash-safe ordering for a passphrase change:
          1) new salt/verifier written to .new files (originals untouched)
          2) rekey_fn(new_key) re-encrypts the DB under the new key
          3) verify_fn(new_key) CONFIRMS the DB actually opens under the new
             key - PRAGMA rekey can silently no-op under a concurrent write
             lock, and swapping identity files after a no-op would strand the
             data under the old key with the old salt gone = permanent loss.
             If it did not take, drop the .new files and raise (originals and
             the old DB key are untouched; the caller keeps its backup).
          4) shelve the old identity, then .new files replace the originals.
        Crash between 1-3: originals + old DB key remain valid (no loss).
        Crash between 3-4: recover_with_db tries the .new salt and completes.
        """
        salt = new_salt()
        key = derive_key(new_passphrase, salt)
        salt_new = self.salt_path.with_name("salt.bin.new")
        ver_new = self.verifier_path.with_name("verifier.bin.new")
        salt_new.write_bytes(salt)
        ver_new.write_bytes(make_verifier(key))
        try:
            rekey_fn(key)
            if not verify_fn(key):
                raise RuntimeError("rekey_did_not_take")
        except Exception:
            for leftover in (salt_new, ver_new):
                leftover.unlink(missing_ok=True)
            raise
        # Rekey confirmed: the old key can no longer open the DB, so shelving
        # (rather than overwriting) the old identity is belt-and-suspenders
        # for the tiny crash window before the replaces land.
        ts = int(time.time())
        for p in (self.salt_path, self.verifier_path):
            if p.exists():
                try:
                    p.replace(p.with_name(f"{p.name}.bak-{ts}"))
                except OSError:
                    pass
        salt_new.replace(self.salt_path)
        ver_new.replace(self.verifier_path)
        return key
