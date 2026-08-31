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
import logging
import os
import secrets
import time
from pathlib import Path

import secure_delete

logger = logging.getLogger(__name__)

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


#: The largest cost this build will derive at. Not a security ceiling - it is
#: the point past which scrypt cannot be called at all on this platform, so
#: values above it are corruption rather than choice. maxmem is computed as
#: 128*n*r + 1 MiB and handed to a C long, which is 4 bytes on Windows, so
#: 128*n*r must stay under LONG_MAX. Measured: n=2**20 with r=8 is exactly
#: 1 GiB and works; n=2**21 overflows before a single byte is allocated.
_MAX_SCRYPT_MEMORY = 2**31 - 1 - 1024 * 1024


def _check_scrypt_bounds(n: int, r: int, p: int) -> None:
    """Refuse parameters that cannot derive a key, before they are used.

    K-06. These came from kdf.json, which is a plain file next to the vault,
    and the only validation was int(). A file saying n=2**21 - one digit away
    from the real 2**17, and exactly what a bit flip in a decimal digit
    produces - passed that check and then made hashlib.scrypt raise.

    That raise is the defect, not the memory. It escaped unlock(), which has
    no try around its derive_key call, so the route never reached the line
    below it where recover_with_db would have healed the vault in one pass
    (it tries the recorded params, then legacy, then current, and rewrites
    kdf.json with whatever opened the database). Measured: the user got a
    plain 500 and "Something went wrong. Please try again." forever, one file
    deletion away from a complete recovery they had no way to know about.

    Refusing here turns that into the case the vault already survives: bad
    parameters read as the legacy ones, the derived key fails the verifier,
    and recovery takes over. Deliberately NOT an exception - read_params
    promises never to raise, and half of the recovery path depends on it.
    """
    if n < 2 or r < 1 or p < 1:
        raise ValueError("scrypt parameters must be positive")
    if n & (n - 1):
        raise ValueError("n must be a power of two")
    if 128 * n * r > _MAX_SCRYPT_MEMORY:
        raise ValueError("scrypt parameters are larger than this build can run")
    # p multiplies the work without multiplying maxmem, so it needs its own
    # bound: OpenSSL checks 128*r*p against maxmem and refuses separately.
    if 128 * r * p > _MAX_SCRYPT_MEMORY:
        raise ValueError("scrypt p is larger than this build can run")


def _scrypt_kwargs(params: dict) -> dict:
    n = int(params["n"])
    r = int(params["r"])
    p = int(params["p"])
    # maxmem is a ceiling, not a request. scrypt needs 128*N*r bytes; leaving
    # it at a fixed 64 MB is what would make raising N fail with a MemoryError
    # instead of costing more, so it is derived from the parameters.
    return dict(n=n, r=r, p=p, dklen=32,
                maxmem=128 * n * r + 1024 * 1024)


def _cost(params: dict) -> dict:
    """The three fields the mirror records, as ints. One place, because two
    spellings of "the cost parameters" drift and the drift is a silent
    rewrite of a file that is only read when everything else is gone."""
    return {field: int(params[field]) for field in ("n", "r", "p")}


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

#: Every file that makes up a vault's IDENTITY, as opposed to its data.
#:
#: The names were spelled out in routers/vault.py's reset path and nowhere
#: else, which is how the launch sweep came to miss them: it knew about
#: `app.db.rekey.bak-*` and nothing about the salt, the verifier or the
#: mirror. This module writes them, so this module is where the list lives.
IDENTITY_NAMES: tuple[str, ...] = (
    "salt.bin", "verifier.bin", "kdf.json", "vault.recovery",
)


def shelved_identity_paths(vault_dir: "Path") -> list["Path"]:
    """The superseded identity files a rotation shelved, in a stable order.

    change_passphrase renames the old salt, verifier and mirror to
    `<name>.bak-<ts>` before swapping the new ones in, and shreds them at the
    end. Between those two points there are forty-two lines of work, and a
    process killed in that window leaves the set behind: taken together they
    are a working recipe for the key the rotation was replacing, sitting in
    the clear next to a database that copy can still open.

    Only ever safe to remove once the LIVE identity is known good, which is
    why the sweep that uses this runs after an unlock has already succeeded.
    Called before that it could shred the only remaining way back.
    """
    out: list["Path"] = []
    for name in IDENTITY_NAMES:
        try:
            out += sorted(vault_dir.glob(f"{name}.bak-*"))
        except OSError:
            continue
    return out


class KeyVault:
    """Manages the passphrase-derived key's identity files (salt + verifier).

    The ultimate authority on key correctness is the encrypted DB itself;
    the verifier is a convenience (fast wrong-passphrase feedback). The
    recovery paths below repair identity files from a DB-validated key.
    """

    def __init__(self, dir_path: Path) -> None:
        #: Names of identity files this vault could not destroy.
        #:
        #: K-07. secure_delete.discard returns a boolean and, at every site in
        #: this file, that boolean was dropped. The material is key material -
        #: a 16-byte salt and its cost parameters are a working recipe for a
        #: key that may still open snapshots on this disk - and the routes
        #: above already have somewhere to put it: change-passphrase answers
        #: with an `unrevoked` list, and it was answering [] while the recipe
        #: for the revoked key sat next to the vault.
        #:
        #: An attribute rather than a return value because change_passphrase
        #: returns the new key and both of its callers assign it directly; a
        #: tuple would have become the key at two call sites, silently.
        self.left_behind: list[str] = []
        self.dir = Path(dir_path)
        self.salt_path = self.dir / "salt.bin"
        self.verifier_path = self.dir / "verifier.bin"
        # Not secret, exactly like the salt: knowing the cost parameters does
        # not help anyone guess the passphrase, and NOT knowing them would
        # make the vault unopenable the moment the default changed.
        self.kdf_path = self.dir / "kdf.json"
        #: A SECOND copy of the salt and the parameters it was derived with.
        #:
        #: salt.bin was a sixteen byte single point of total loss, and that
        #: was measured rather than feared: delete it and recover_with_db
        #: returns None with the correct passphrase in hand, because scrypt
        #: needs that exact salt and the loop looking for one had exactly two
        #: entries. Flip one byte and the same thing happens, except the file
        #: is still sitting there looking healthy. verifier.bin and kdf.json
        #: both already survive their own loss; the salt did not.
        #:
        #: NOT named to hide. A file called app.db.idx would fool a person for
        #: about a minute and would fool the next maintainer into deleting it
        #: as a regenerable index, which is the opposite of the point. It is
        #: named for what it is, and the accepted limit is stated plainly: a
        #: person who reads this and removes both copies has removed both
        #: copies. What it defends against is the accident, the single byte,
        #: and the program that surgically removes one known filename.
        #:
        #: It carries NO VERIFIER, and the reason is narrower than a first
        #: draft of this comment claimed. That draft said omitting the
        #: verifier is what keeps a passphrase change revoking the old key.
        #: It is not: recover_with_db MANUFACTURES a verifier from whatever
        #: key the mirrored salt produces, so the oracle comes back the
        #: moment the mirror is used. What omitting it actually buys is that
        #: this one file is not, on its own, the complete offline attack kit.
        #:
        #: What keeps a rotation honest is the read-back in
        #: change_passphrase: a mirror that could not be replaced still
        #: describes the revoked salt, and the rotation says so.
        self.mirror_path = self.dir / "vault.recovery"

    # -- state ---------------------------------------------------------------
    def is_initialized(self) -> bool:
        """True once a passphrase has been set (salt + verifier exist)."""
        return self.salt_path.exists() and self.verifier_path.exists()

    def can_derive(self) -> bool:
        """Salt still present - a passphrase key can still be derived."""
        return self.salt_path.exists()

    def can_recover(self) -> bool:
        """Whether ANY salt is on disk, staged or live.

        K-05. The unlock route gated on can_derive(), which asks only about
        salt.bin - but recover_with_db looks at salt.bin AND salt.bin.new, so
        the route declared impossible the one state recovery was written for.

        That state is reachable: change_passphrase shelves salt.bin before
        verifier.bin, so a crash between those two renames leaves no salt.bin,
        a correct salt.bin.new, and a database already re-keyed. The vault was
        then wedged - status said "not initialized" so the UI offered setup,
        init answered 409 because the database is encrypted, and unlock
        answered 409 because salt.bin was gone. Every door shut, on a vault
        whose data was intact and whose passphrase the user knew.

        The mirror joins the same list for the same reason. Without it the
        route would answer "not initialized" for a vault whose only surviving
        salt is the one written to survive exactly this, and the repair would
        be correct, present, and unreachable, which is K-05 verbatim.
        """
        return (self.salt_path.exists()
                or self.salt_path.with_name("salt.bin.new").exists()
                or self._read_mirror() is not None)

    # -- the salt mirror ------------------------------------------------------
    def _write_mirror(self, salt: bytes, params: dict,
                      path: Path | None = None) -> None:
        """Stage, flush, replace. Never a partial file under the live name.

        JSON rather than the raw sixteen bytes, and that is not decoration: a
        half-written JSON object fails to parse and is skipped, while a
        half-written raw salt is indistinguishable from a good one and would
        be tried, fail db_check, and read as a wrong passphrase.

        Carries the parameters WITH the salt. A salt recovered under the wrong
        cost derives a key that opens nothing, which is the exact bug the
        comment above the recover_with_db loop already documents for
        salt.bin.new.

        Best effort by contract: every caller is in the middle of a sequence
        that must not fail because a second copy could not be written.
        """
        target = path or self.mirror_path
        body = json.dumps({"kdf": "scrypt", "salt": salt.hex(),
                           **{f: int(params[f]) for f in ("n", "r", "p")}})
        # When a caller names the target it is ALREADY a staging file it will
        # replace itself, so this writes it in place. Staging a staging file
        # produced `vault.recovery.new.new`, a name nothing in the tree knew:
        # _reset_identity_files enumerates `<name>`, `<name>.new` and
        # `<name>.bak-*`, so a wipe left it behind, and what it left behind is
        # a self-describing JSON object holding the salt and its parameters.
        staged = target if path is not None else target.with_name(
            f"{target.name}.new")
        try:
            with open(staged, "w", encoding="utf-8") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            if staged != target:
                staged.replace(target)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Not silent. A mirror that could not be written is the whole
            # protection quietly not existing, and the class this file exists
            # for is the loss nobody was told about.
            logger.warning(
                "vault: could not write the salt mirror (%s); the vault is "
                "back to having exactly one copy of its salt",
                type(exc).__name__)

    def _read_mirror(self, path: Path | None = None) -> tuple[bytes, dict] | None:
        """The mirrored salt and its parameters, or None for anything wrong.

        Returns None rather than raising for every failure - absent, torn,
        not JSON, a salt that is not hex, parameters outside the bounds
        _check_scrypt_bounds enforces. A mirror is a convenience; a mirror
        that can throw would turn a recoverable vault into a 500.

        It is also never TRUSTED. db_check is the only authority on whether a
        key is right, so a stale or hostile mirror simply fails it and costs
        one derivation.
        """
        target = path or self.mirror_path
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            salt = bytes.fromhex(data["salt"])
            params = {field: int(data[field]) for field in ("n", "r", "p")}
            _check_scrypt_bounds(**params)
        except Exception:                                # noqa: BLE001
            return None
        if not salt:
            return None
        return salt, params

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
            checked = {field: int(data[field]) for field in ("n", "r", "p")}
            _check_scrypt_bounds(**checked)
            # The CHECKED integers, not the raw values. Before this, a float
            # or a numeric string was validated and then the original handed
            # to scrypt, so the thing tested was not the thing used.
            return {**KDF_LEGACY, **data, **checked}
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
        for p in (self.salt_path, self.verifier_path,
                  self.kdf_path, self.mirror_path):
            if p.exists():
                try:
                    p.replace(p.with_name(f"{p.name}.bak-{ts}"))
                except OSError:
                    pass
        salt = new_salt()
        params = dict(KDF_CURRENT)
        key = derive_key(passphrase, salt, params)
        # PARAMETERS FIRST, and the order is the whole point.
        #
        # is_initialized() is "salt.bin and verifier.bin both exist", so those
        # two writes are what makes the vault real. Writing kdf.json after
        # them left a window in which the vault WAS initialized and its
        # parameters were not recorded: read_params falls back to the legacy
        # cost, unlock derives a key that fails the verifier, init answers 409
        # because the vault already exists, and recovery cannot help because
        # there is no database yet for db_check to open. A dead end, from one
        # crash between two adjacent lines.
        #
        # Parameters alone are inert - nothing reads them without a salt - so
        # with this order every crash window here is simply re-runnable
        # through /vault/init. kdf.json joins the shelving loop above for the
        # same reason: a re-init used to overwrite the parameters describing
        # the salt it had just moved aside.
        self.write_params(params)
        self.salt_path.write_bytes(salt)
        # After the live salt, never before. A mirror describing a salt that
        # does not exist yet would be tried first by a recovery that ran in
        # the crash window between these two lines, and would hand back a key
        # for a database that had not been created with it.
        self._write_mirror(salt, params)
        self.verifier_path.write_bytes(make_verifier(key))
        return key

    # -- later runs: unlock ---------------------------------------------------
    def unlock(self, passphrase: str) -> bytes | None:
        """Return the key if the passphrase is correct, else None."""
        if not self.is_initialized():
            return None
        try:
            salt = self.salt_path.read_bytes()
            params = self.read_params()
            key = derive_key(passphrase, salt, params)
            if check_verifier(key, self.verifier_path.read_bytes()):
                # The mirror rots silently otherwise. Nothing else on the
                # ordinary path touches disk, so a mirror deleted while every
                # other file is healthy would stay deleted forever and the
                # protection would simply not exist on the day it was needed.
                # Inside its own try because unlock promises not to throw.
                try:
                    if self._read_mirror() != (salt, _cost(params)):
                        self._write_mirror(salt, params)
                except Exception:                        # noqa: BLE001
                    pass
                return key
        except Exception as exc:                         # noqa: BLE001
            # "unlock does not throw; recovery takes over" is the contract the
            # route depends on, and it was not kept. An unreadable salt.bin,
            # an unreadable verifier.bin, or parameters scrypt refuses all
            # escaped to a 500 - and the 500 happened one line ABOVE the call
            # to recover_with_db, which already survives every one of them
            # (its own derive_key sits in except Exception: continue). So the
            # repair existed, was correct, and was unreachable.
            #
            # The class name only, never the traceback and never the message.
            # This module's first promise is that a passphrase is not logged
            # here, and the passphrase is an argument to the frame that threw.
            logger.warning(
                "vault: could not derive from the stored identity (%s); "
                "falling through to recovery", type(exc).__name__)
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
        mirror_new = self.mirror_path.with_name(
            f"{self.mirror_path.name}.new")
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
                        # The salt being replaced describes a key that may
                        # still open snapshots on this disk, so it leaves by
                        # the same door change_passphrase uses for the shelved
                        # identity - overwritten, not handed back to the
                        # filesystem with its bytes intact. This line used to
                        # be a plain write_bytes, and K-05 makes it common.
                        if self.salt_path.exists():
                            self.left_behind.extend(
                                [] if secure_delete.discard(self.salt_path)
                                else [self.salt_path.name])
                        self.salt_path.write_bytes(salt)
                    self.verifier_path.write_bytes(make_verifier(key))
                    self.write_params(params)
                    self._write_mirror(salt, params)
                    for leftover in (salt_new, ver_new, kdf_new,
                                    mirror_new):
                        if leftover.exists() and not secure_delete.discard(
                                leftover):
                            # K-07. discard returns False and says nothing for
                            # an ordinary OSError, and this sat inside an
                            # except OSError that a False return never even
                            # reaches. A staged identity surviving here is the
                            # recipe for a key, left beside the vault.
                            self.left_behind.append(leftover.name)
                except OSError:
                    pass
                return key
        # The mirror, LAST, so the ordinary case and the half-finished
        # rotation keep the cost they have today. It is not trusted for a
        # moment: db_check is the only authority on whether a key is right, so
        # a stale or hostile mirror costs one derivation and is skipped. That
        # is the whole reason a second COPY is safe where a second source of
        # truth would not be.
        mirrored = self._read_mirror()
        if mirrored is not None:
            salt, params = mirrored
            try:
                key = derive_key(passphrase, salt, params)
            except Exception:                            # noqa: BLE001
                key = None
            if key is not None and db_check(key):
                try:
                    if self.salt_path.exists():
                        # The salt being replaced may still open snapshots on
                        # this disk, so it leaves by the same door as every
                        # other superseded identity file rather than being
                        # handed back to the filesystem with its bytes intact.
                        self.left_behind.extend(
                            [] if secure_delete.discard(self.salt_path)
                            else [self.salt_path.name])
                    self.salt_path.write_bytes(salt)
                    self.verifier_path.write_bytes(make_verifier(key))
                    self.write_params(params)
                    # The same sweep the staged-salt branch above does. Its
                    # absence here was measured: recovering through the mirror
                    # left the whole staged identity family on disk,
                    # unshredded and unreported, which is the K-07 defect in a
                    # new place.
                    for leftover in (salt_new, ver_new, kdf_new, mirror_new):
                        if leftover.exists() and not secure_delete.discard(
                                leftover):
                            self.left_behind.append(leftover.name)
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
        mirror_new = self.mirror_path.with_name(
            f"{self.mirror_path.name}.new")
        salt_new.write_bytes(salt)
        ver_new.write_bytes(make_verifier(key))
        self.write_params(params, kdf_new)
        self._write_mirror(salt, params, mirror_new)
        try:
            rekey_fn(key)
            if not verify_fn(key):
                raise RuntimeError("rekey_did_not_take")
        except Exception:
            for leftover in (salt_new, ver_new, kdf_new, mirror_new):
                # The half-written NEW identity. Same material as the old one,
                # so it leaves by the same door.
                if not secure_delete.discard(leftover):
                    self.left_behind.append(leftover.name)
            raise
        # Rekey confirmed: the old key can no longer open the DB, so shelving
        # (rather than overwriting) the old identity is belt-and-suspenders
        # for the tiny crash window before the replaces land.
        ts = int(time.time())
        for p in (self.salt_path, self.verifier_path, self.mirror_path):
            if p.exists():
                try:
                    p.replace(p.with_name(f"{p.name}.bak-{ts}"))
                except OSError:
                    pass
        salt_new.replace(self.salt_path)
        # With the salt and before the verifier, so the mirror never describes
        # a salt the live file has not reached yet.
        try:
            mirror_new.replace(self.mirror_path)
        except OSError:
            pass
        # READ IT BACK, and this is not belt and braces. Both renames that
        # touch this name - the shelve above and the replace here - go through
        # the same file, so ONE open handle on vault.recovery fails both, and
        # a handle is exactly what a backup agent, an indexer or an antivirus
        # scanner holds. The rotation then completes loudly and correctly for
        # salt.bin and verifier.bin while the mirror still describes the salt
        # this rotation just revoked, which is a working recipe for the old
        # key sitting beside snapshots still encrypted under it.
        #
        # Measured before this line existed: the route answered
        # {"unrevoked": []} while recover_with_db opened the vault again with
        # the OLD passphrase. left_behind is the list the route already puts
        # on the wire, so the failure goes there rather than into a log.
        if self._read_mirror() != (salt, _cost(params)):
            self.left_behind.append(self.mirror_path.name)
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
        for p in (self.salt_path, self.verifier_path, self.mirror_path):
            # unlink left the recipe recoverable, which is most of the way to
            # not having removed it: scrypt params plus a 16-byte salt is a
            # small, distinctive pattern for an undelete tool to find beside a
            # snapshot still encrypted under the key it derives.
            shelved = p.with_name(f"{p.name}.bak-{ts}")
            if not secure_delete.discard(shelved):
                # The single most important one to say out loud. If this file
                # survives, the rotation revoked nothing for anyone holding
                # the old passphrase - and the route answered {"unrevoked":
                # []} while it sat there. The caller reads this list and puts
                # it on the wire beside the snapshots it could not re-key.
                self.left_behind.append(shelved.name)
        return key
