"""The other full copy of the vault, that nobody could see or remove.

An interrupted migration leaves app.db.enc-tmp: a complete, encrypted copy of
everything. Adoption reclaims it when the live database is missing or empty,
and deliberately declines in the only two other cases - the live database is
healthy (so this is a redundant duplicate), or the copy does not open under
this key (so it may be a vault under a DIFFERENT passphrase).

Either way it stayed forever. /vault/status reported a boolean nothing in the
frontend rendered, and no route could remove it. That is the same shape as the
plaintext backup, with one difference that changes the design: this file is
encrypted, so "can this user read it" is a real question - and the answer
decides whether deleting is tidying up or destroying the only copy of
something.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import config
import database
import vault_state


def _orphan(readable: bool = True) -> Path:
    """A stranded encrypted copy beside the live database.

    readable=True copies the live vault, so it opens under the current key -
    exactly what an interrupted migration leaves. readable=False writes bytes
    that do not, standing in for a copy keyed to another passphrase.
    """
    live = Path(config.DB_PATH)
    # Fails loudly rather than reading the developer's own vault. The suite
    # redirects DB_PATH per test; if that ever stops happening this line is
    # the difference between a red test and a silent copy of real content.
    assert "pytest" in str(live).lower() or "tmp" in str(live).lower(), (
        f"DB_PATH was not redirected to a temporary file: {live}")
    orphan = live.with_name(live.name + ".enc-tmp")
    orphan.write_bytes(live.read_bytes() if readable
                       else b"SQLite format 3\x00" + os.urandom(4096))
    return orphan


@pytest.fixture(autouse=True)
def _no_stray_orphan(db):
    """Depends on `db` ON PURPOSE, and that is the whole point of the argument.

    Without it this fixture is autouse with no dependency, so pytest is free
    to set it up before `db` monkeypatches DB_PATH - and then its teardown
    globs and UNLINKS in the real data folder beside the owner's own vault.
    It also made `_orphan(readable=True)` read the live database, which is
    the one file this suite exists to never touch.
    """
    yield
    live = Path(config.DB_PATH)
    for stray in live.parent.glob(live.name + database.ORPHAN_GLOB):
        stray.unlink(missing_ok=True)


class TestTheCopyIsVisible:
    def test_status_reports_it(self, client) -> None:
        _orphan()
        body = client.get("/api/v1/vault/status").json()
        assert body["orphaned_copy"] is True

    def test_status_says_whether_this_vault_can_read_it(self, client) -> None:
        # The field that decides what the user may safely do with it.
        _orphan(readable=True)
        assert client.get(
            "/api/v1/vault/status").json()["orphaned_copy_readable"] is True

    def test_a_copy_under_another_key_is_reported_as_unreadable(
        self, client
    ) -> None:
        _orphan(readable=False)
        assert client.get(
            "/api/v1/vault/status").json()["orphaned_copy_readable"] is False

    def test_readability_is_unknown_while_locked(self, client) -> None:
        # null, not False. Answering the question needs the key, and "we did
        # not look" must not read as "we looked and it is unreadable" - the
        # second would invite deleting something this vault cannot open.
        _orphan()
        vault_state.clear_key()
        try:
            body = client.get("/api/v1/vault/status").json()
            assert body["orphaned_copy"] is True
            assert body["orphaned_copy_readable"] is None
        finally:
            from tests.conftest import TEST_VAULT_KEY
            vault_state.set_key(TEST_VAULT_KEY)

    def test_nothing_is_claimed_when_there_is_no_copy(self, client) -> None:
        body = client.get("/api/v1/vault/status").json()
        assert body["orphaned_copy"] is False
        assert body["orphaned_copy_readable"] is None


class TestTheCopyCanBeRemoved:
    def test_a_readable_duplicate_is_shredded(self, client) -> None:
        orphan = _orphan(readable=True)
        body = client.post("/api/v1/vault/discard-orphaned-copy").json()
        assert body == {"removed": True, "reason": ""}
        assert not orphan.exists()

    def test_the_content_is_overwritten_not_just_unlinked(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orphan = _orphan(readable=True)
        original = orphan.read_bytes()
        monkeypatch.setattr(os, "unlink", lambda *a, **kw: None)

        client.post("/api/v1/vault/discard-orphaned-copy")

        survivor = orphan.read_bytes()
        assert len(survivor) == len(original)
        assert survivor != original

    def test_status_stops_reporting_it(self, client) -> None:
        _orphan()
        client.post("/api/v1/vault/discard-orphaned-copy")
        body = client.get("/api/v1/vault/status").json()
        assert body["orphaned_copy"] is False

    def test_discarding_nothing_is_not_an_error(self, client) -> None:
        assert client.post("/api/v1/vault/discard-orphaned-copy").json() == {
            "removed": False, "reason": "not_present"}


class TestItRefusesWhatItCannotRead:
    def test_a_copy_under_another_key_is_left_alone(self, client) -> None:
        # THE safety property. Every other deletion in this app removes
        # something the user can already read. This one would not - an
        # encrypted file nobody here can open is not clutter, it is data whose
        # passphrase we do not have.
        orphan = _orphan(readable=False)
        before = orphan.read_bytes()

        body = client.post("/api/v1/vault/discard-orphaned-copy").json()

        assert body == {"removed": False, "reason": "different_key"}
        assert orphan.read_bytes() == before

    def test_it_refuses_while_locked(self, client) -> None:
        # Without the key the two cases cannot be told apart, so it does not
        # guess. The plaintext backup route is open while locked precisely
        # because that file needs no key to read; this one does.
        orphan = _orphan()
        vault_state.clear_key()
        try:
            response = client.post("/api/v1/vault/discard-orphaned-copy")
            assert response.status_code == 423
            assert orphan.exists()
        finally:
            from tests.conftest import TEST_VAULT_KEY
            vault_state.set_key(TEST_VAULT_KEY)


class TestItRemovesNothingElse:
    def test_the_live_vault_is_never_touched(self, client) -> None:
        live = Path(config.DB_PATH)
        before = live.read_bytes()
        _orphan()

        client.post("/api/v1/vault/discard-orphaned-copy")

        assert live.read_bytes() == before

    def test_a_plaintext_backup_beside_it_is_left_for_its_own_route(
        self, client
    ) -> None:
        live = Path(config.DB_PATH)
        backup = live.with_name(live.name + ".plain.bak-20260101120000")
        backup.write_bytes(b"a different problem")
        _orphan()
        try:
            client.post("/api/v1/vault/discard-orphaned-copy")
            assert backup.exists()
        finally:
            backup.unlink(missing_ok=True)


class TestRotatingThePassphraseRevokesThisCopyToo:
    """A rotation is a revocation, and this copy was outside it.

    _rekey_sidecars swept `app.db*.bak*`. app.db.enc-tmp has no "bak" in its
    name, so a complete vault - every chat, persona, secret and image - stayed
    openable with the passphrase the user was rotating AWAY from, while the
    route answered {"unrevoked": []}: a clean rotation, reported honestly,
    that had revoked nothing about this file.

    That is the exact failure the function's own docstring says it exists to
    prevent, for a filename it did not think of.
    """

    def test_the_old_key_no_longer_opens_it_after_a_rotation(
        self, client
    ) -> None:
        from routers.vault import _rekey_sidecars
        from tests.conftest import TEST_VAULT_KEY

        orphan = _orphan(readable=True)
        old_key = TEST_VAULT_KEY
        new_key = bytes(range(32, 64))
        live = Path(config.DB_PATH)

        unrevoked = _rekey_sidecars(live, live, old_key, new_key)

        assert unrevoked == [], f"rotation could not revoke: {unrevoked}"
        assert not database.check_key(old_key, str(orphan)), (
            "the stranded copy still opens with the passphrase that was "
            "just rotated away")
        assert database.check_key(new_key, str(orphan))

    def test_an_empty_stub_is_not_reported_as_unrevoked(self, client) -> None:
        # adopt_orphaned_enc_tmp leaves app.db.empty-stub-bak: zero bytes, so
        # there is no ciphertext to re-key. It matches the *.bak* sweep, and
        # failing on it would raise an alarm about a file holding nothing -
        # teaching the user to ignore the one field that must be believed.
        from routers.vault import _rekey_sidecars
        from tests.conftest import TEST_VAULT_KEY

        live = Path(config.DB_PATH)
        stub = live.with_name(live.name + ".empty-stub-bak")
        stub.touch()
        try:
            unrevoked = _rekey_sidecars(live, live, TEST_VAULT_KEY,
                                        bytes(range(32, 64)))
            assert stub.name not in unrevoked
        finally:
            stub.unlink(missing_ok=True)


class TestAJournalFileIsNotACopy:
    """K-50. `ORPHAN_GLOB` is `.enc-tmp*`, and that also catches
    `app.db.enc-tmp-wal` - a journal ATTACH can leave behind, which is not a
    database and opens under no key at all.

    Both readers below insist that EVERY match opens under the current key,
    and they are right to: one unreadable file among several is precisely the
    case that must not be offered a delete button. So the two correct rules
    multiplied into a wrong answer - one stray journal file answered
    `different_key` forever and permanently disabled the only route that can
    remove a stranded copy of the vault.
    """

    def _sidecars_beside(self, orphan: Path) -> list[Path]:
        planted = []
        for suffix in ("-wal", "-shm", "-journal"):
            side = orphan.with_name(orphan.name + suffix)
            side.write_bytes(b"not a database, just pages" * 8)
            planted.append(side)
        return planted

    def test_a_stray_journal_does_not_veto_the_route(self, client) -> None:
        orphan = _orphan(readable=True)
        self._sidecars_beside(orphan)
        body = client.get("/api/v1/vault/status").json()
        # Still one copy, still readable - the journals said nothing about it.
        assert body["orphaned_copy"] is True
        assert body["orphaned_copy_readable"] is True
        assert client.post(
            "/api/v1/vault/discard-orphaned-copy").status_code == 200
        assert not orphan.exists()

    def test_the_listing_answers_copies_and_only_copies(self) -> None:
        """Straight at the function, because the route test above would also
        stay green if the journals merely stopped EXISTING for some unrelated
        reason - and they do: `check_key` opens the copy, and SQLite clears a
        hot journal on the way. That side effect is not this guard, and a test
        that cannot tell them apart proves nothing about either."""
        orphan = _orphan(readable=True)
        self._sidecars_beside(orphan)
        assert [p.name for p in database.orphaned_enc_tmp_paths()] == [orphan.name]

    def test_an_unreadable_COPY_still_vetoes_the_route(self, client) -> None:
        """The safety property this fix must not weaken. A file that does not
        open may be a vault under a passphrase we do not have, and forgiving
        it would be the whole guard gone."""
        _orphan(readable=False)
        assert client.get(
            "/api/v1/vault/status").json()["orphaned_copy_readable"] is False
        body = client.post("/api/v1/vault/discard-orphaned-copy").json()
        assert body == {"removed": False, "reason": "different_key"}
