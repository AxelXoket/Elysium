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
import json
import secrets
import time
from pathlib import Path

import secure_delete

# ---------------------------------------------------------------- scrypt KDF

# Memory-hard params. Runs ONCE per unlock; the engine gets the raw key
# (PRAGMA key = "x'...'"), so per-connection KDF cost is zero.
#
# The parameters are RECORDED PER VAULT, in kdf.json beside the salt, and that
# is the point of this block. They were a module constant, which made them
# unchangeable in practice: raising them changes every derived key, so every
# existing vault would have stopped opening. A cost setting nobody can raise
# is a cost setting frozen at whatever seemed enough the year it was written.
#
# v1 was N=2^15 (32 MB). OWASP's current floor for scrypt is N=2^17, r=8, p=1
# (128 MB), which is where v2 sits. Measured on this machine: 0.062s at v1,
# 0.242s at v2 - a quarter of a second, once, at unlock, for four times the
# memory and four times the work per guess an attacker has to pay.
KDF_V1: dict = {"kdf": "scrypt", "v": 1, "n": 2**15, "r": 8, "p": 1}
KDF_CURRENT: dict = {"kdf": "scrypt", "v": 2, "n": 2**17, "r": 8, "p": 1}

#: What a vault with no kdf.json is: every vault created before this existed.
KDF_LEGACY = KDF_V1


def _scrypt_kwargs(params: dict) -> dict:
    n = int(params["n"])
    r = int(params["r"])
    p = int(params["p"])
    # maxmem is a ceiling, not a request. scrypt needs 128*N*r bytes; leaving
    # it at a fixed 64 MB is what would make raising N fail with a MemoryError
    # instead of costing more, so it is derived from the parameters.
    return dict(n=n, r=r, p=p, dklen=32,
                maxmem=128 * n * r + 1024 * 1024)


def new_salt() -> bytes:
    return secrets.token_bytes(16)


def derive_key(passphrase: str, salt: bytes, params: dict | None = None
               ) -> bytes:
    """Derive the 32-byte DB key from the passphrase (never stored)."""
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt,
                          **_scrypt_kwargs(params or KDF_LEGACY))


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
        # Not secret, exactly like the salt: knowing the cost parameters does
        # not help anyone guess the passphrase, and NOT knowing them would
        # make the vault unopenable the moment the default changed.
        self.kdf_path = self.dir / "kdf.json"

    # -- state ---------------------------------------------------------------
    def is_initialized(self) -> bool:
        """True once a passphrase has been set (salt + verifier exist)."""
        return self.salt_path.exists() and self.verifier_path.exists()

    def can_derive(self) -> bool:
        """Salt still present - a passphrase key can still be derived."""
        return self.salt_path.exists()

    # -- KDF parameters -------------------------------------------------------
    def read_params(self, path: Path | None = None) -> dict:
        """The parameters THIS vault's key was derived with.

        A missing or unreadable file means the legacy parameters, never the
        current ones. Getting that default wrong in the other direction would
        derive a key that does not open the database and report it to the user
        as a wrong passphrase - so the failure mode is chosen deliberately.
        """
        target = path or self.kdf_path
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
            for field in ("n", "r", "p"):
                int(data[field])
            return {**KDF_LEGACY, **data}
        except Exception:                                # noqa: BLE001
            return dict(KDF_LEGACY)

    def write_params(self, params: dict, path: Path | None = None) -> None:
        (path or self.kdf_path).write_text(
            json.dumps(params, sort_keys=True), encoding="utf-8")

    def needs_kdf_upgrade(self) -> bool:
        """Whether this vault is still on parameters weaker than current."""
        current = self.read_params()
        return (int(current.get("n", 0)) < int(KDF_CURRENT["n"])
                or int(current.get("r", 0)) < int(KDF_CURRENT["r"])
                or int(current.get("p", 0)) < int(KDF_CURRENT["p"]))

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
        params = dict(KDF_CURRENT)
        key = derive_key(passphrase, salt, params)
        self.salt_path.write_bytes(salt)
        self.verifier_path.write_bytes(make_verifier(key))
        self.write_params(params)
        return key

    # -- later runs: unlock ---------------------------------------------------
    def unlock(self, passphrase: str) -> bytes | None:
        """Return the key if the passphrase is correct, else None."""
        if not self.is_initialized():
            return None
        salt = self.salt_path.read_bytes()
        key = derive_key(passphrase, salt, self.read_params())
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
        kdf_new = self.kdf_path.with_name("kdf.json.new")
        # Each candidate salt is paired with the parameters it was written
        # WITH. A half-finished change stages both, and deriving the staged
        # salt under the live parameters would produce a key that opens
        # nothing - which this function would read as "wrong passphrase".
        for sp, kp in ((self.salt_path, self.kdf_path), (salt_new, kdf_new)):
            if not sp.exists():
                continue
            for params in self._candidate_params(kp):
                try:
                    salt = sp.read_bytes()
                    key = derive_key(passphrase, salt, params)
                except Exception:
                    continue
                if not db_check(key):
                    continue
                try:  # best-effort repair: a write error must not block entry
                    if sp != self.salt_path:
                        self.salt_path.write_bytes(salt)
                    self.verifier_path.write_bytes(make_verifier(key))
                    self.write_params(params)
                    for leftover in (salt_new, ver_new, kdf_new):
                        if leftover.exists():
                            secure_delete.discard(leftover)
                except OSError:
                    pass
                return key
        return None

    def _candidate_params(self, kdf_path: Path) -> list[dict]:
        """The recorded parameters, and the legacy ones as a fallback.

        A vault whose kdf.json was lost still has to open. The recorded set is
        tried first so the ordinary case costs one derivation; the legacy set
        is the answer for every vault created before the file existed, which
        is what read_params already defaults to - the second entry only
        matters when a kdf.json exists but does not describe this salt.
        """
        recorded = self.read_params(kdf_path)
        candidates = [recorded]
        if recorded != KDF_LEGACY:
            candidates.append(dict(KDF_LEGACY))
        if recorded != KDF_CURRENT:
            candidates.append(dict(KDF_CURRENT))
        return candidates

    # -- change passphrase -----------------------------------------------------
    def change_passphrase(self, new_passphrase: str, rekey_fn, verify_fn,
                          params: dict | None = None) -> bytes:
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
        # Every rotation lands on the current parameters. A user who changes
        # their passphrase should not keep the cost settings of whenever the
        # vault was first created, and staging the file with the salt keeps
        # the two describing the same key at every point in the sequence.
        params = dict(params or KDF_CURRENT)
        key = derive_key(new_passphrase, salt, params)
        salt_new = self.salt_path.with_name("salt.bin.new")
        ver_new = self.verifier_path.with_name("verifier.bin.new")
        kdf_new = self.kdf_path.with_name("kdf.json.new")
        salt_new.write_bytes(salt)
        ver_new.write_bytes(make_verifier(key))
        self.write_params(params, kdf_new)
        try:
            rekey_fn(key)
            if not verify_fn(key):
                raise RuntimeError("rekey_did_not_take")
        except Exception:
            for leftover in (salt_new, ver_new, kdf_new):
                # The half-written NEW identity. Same material as the old one,
                # so it leaves by the same door.
                secure_delete.discard(leftover)
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
        # AFTER the salt, never before. Between these two lines the vault
        # would describe the new salt with the old parameters; the reverse
        # order would describe the OLD salt with the new ones, and a crash
        # there leaves a vault that derives a key opening nothing.
        kdf_new.replace(self.kdf_path)
        # The shelved identity covered the crash window that just closed. Left
        # on disk it is a working recipe for the OLD key - same scrypt params,
        # same 16-byte salt - sitting beside any encrypted snapshot still under
        # that key, so a rotation would revoke nothing for anyone who knew the
        # previous passphrase. The caller re-keys the snapshots; this removes
        # the recipe.
        for p in (self.salt_path, self.verifier_path):
            # unlink left the recipe recoverable, which is most of the way to
            # not having removed it: scrypt params plus a 16-byte salt is a
            # small, distinctive pattern for an undelete tool to find beside a
            # snapshot still encrypted under the key it derives.
            secure_delete.discard(p.with_name(f"{p.name}.bak-{ts}"))
        return key
