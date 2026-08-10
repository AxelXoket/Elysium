"""The unencrypted copy of everything, that nobody could see and nobody could delete.

Migrating a pre-vault database renames the old plaintext app.db to
app.db.plain.bak-<ts> and keeps it. That is the right call at the time: a
migration that verified wrong would otherwise have destroyed the only copy of
everything the user ever wrote.

What was wrong is what came after. Nothing removed it, and nothing reported
it. The user saw one banner, on the one launch that migrated, and from then on
the file was invisible - no field in /vault/status, nothing in settings, no
route to remove it. A complete SQLite database with every message, every
character card and every system prompt, in the clear, beside a UI calling the
vault encrypted.

So: a state the UI can show, and a door the user can open.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import config
import database


def _backup(stamp: str = "20260101120000") -> Path:
    """A stand-in for the pre-vault database, beside the live one.

    Named from the live database rather than hardcoded: migration builds the
    name as `<db>.plain.bak-<ts>`, and a fixture that assumed "app.db" passed
    against a suite whose database is called something else - proving only
    that the glob found nothing.
    """
    live = Path(config.DB_PATH)
    path = live.with_name(f"{live.name}.plain.bak-{stamp}")
    path.write_bytes(b"SQLite format 3\x00" + b"selamlar madeline" * 64)
    return path


@pytest.fixture(autouse=True)
def _no_stray_backups():
    # The fixture DB lives in a tmp dir per test, but be explicit: a leftover
    # from another test would make these pass for the wrong reason.
    yield
    for stray in database.plaintext_backups():
        stray.unlink(missing_ok=True)


class TestTheCopyIsVisible:
    def test_status_reports_a_plaintext_backup(self, client) -> None:
        backup = _backup()
        body = client.get("/api/v1/vault/status").json()
        assert body["plaintext_backups"] == [backup.name]

    def test_status_says_nothing_when_there_is_nothing(self, client) -> None:
        assert client.get("/api/v1/vault/status").json()["plaintext_backups"] == []

    def test_more_than_one_migration_is_all_reported(self, client) -> None:
        # Re-running a migration makes a second timestamped file. Reporting
        # only the first would leave the rest exactly as invisible as before.
        first = _backup("20260101120000")
        second = _backup("20260202130000")
        body = client.get("/api/v1/vault/status").json()
        assert body["plaintext_backups"] == sorted([first.name, second.name])


class TestTheCopyCanBeRemoved:
    def test_discarding_deletes_it(self, client) -> None:
        backup = _backup()
        body = client.post("/api/v1/vault/discard-plaintext-backup").json()
        assert body == {"removed": 1, "left": []}
        assert not backup.exists()

    def test_the_content_is_overwritten_not_just_unlinked(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The point is that the messages stop existing, not that the directory
        # entry does. Hold the unlink so what is left can be read back.
        backup = _backup()
        original = backup.read_bytes()
        monkeypatch.setattr(os, "unlink", lambda *a, **kw: None)

        client.post("/api/v1/vault/discard-plaintext-backup")

        survivor = backup.read_bytes()
        assert len(survivor) == len(original)
        assert b"madeline" not in survivor

    def test_status_stops_reporting_it_afterwards(self, client) -> None:
        _backup()
        client.post("/api/v1/vault/discard-plaintext-backup")
        assert client.get("/api/v1/vault/status").json()["plaintext_backups"] == []

    def test_discarding_nothing_is_not_an_error(self, client) -> None:
        assert client.post("/api/v1/vault/discard-plaintext-backup").json() == {
            "removed": 0, "left": []}

    @pytest.mark.skipif(os.name != "nt", reason="Windows file locking")
    def test_a_file_it_could_not_delete_is_named(self, client) -> None:
        # A route that answered a flat "done" while a full plaintext database
        # stayed readable would make exactly the promise this exists to keep.
        backup = _backup()
        with open(backup, "rb"):
            body = client.post("/api/v1/vault/discard-plaintext-backup").json()
        assert body["removed"] == 0
        assert body["left"] == [backup.name]
        assert backup.exists()


class TestItRemovesNothingElse:
    def test_the_live_vault_is_never_touched(self, client) -> None:
        live = Path(config.DB_PATH)
        before = live.read_bytes()
        _backup()

        client.post("/api/v1/vault/discard-plaintext-backup")

        assert live.exists()
        assert live.read_bytes() == before

    def test_the_identity_files_are_never_touched(self, client) -> None:
        # salt.bin and verifier.bin sit in the same folder and a glob one
        # character too greedy would take them - which destroys the vault.
        # The identity files are written by /vault/init, which the fixtures
        # deliberately skip - they set the key directly. So this read them
        # "if they exist", found neither, and looped over an empty dict: the
        # ONE test standing between a greedy glob and an unopenable vault
        # asserted nothing at all, on every run. Plant them instead.
        folder = Path(config.DB_PATH).parent
        neighbours = {
            name: f"identity-{name}".encode()
            for name in ("salt.bin", "verifier.bin", "kdf.json")
        }
        for name, content in neighbours.items():
            (folder / name).write_bytes(content)
        _backup()

        client.post("/api/v1/vault/discard-plaintext-backup")

        for name, content in neighbours.items():
            assert (folder / name).read_bytes() == content, f"{name} was damaged"

    def test_an_unrelated_neighbour_survives(self, client) -> None:
        folder = Path(config.DB_PATH).parent
        decoy = Path(config.DB_PATH).with_suffix(
            Path(config.DB_PATH).suffix + ".enc-tmp")
        decoy.write_bytes(b"an interrupted migration, not a plaintext backup")
        _backup()

        client.post("/api/v1/vault/discard-plaintext-backup")

        assert decoy.exists(), "the orphaned encrypted copy was destroyed"
        decoy.unlink()


@pytest.mark.skipif(os.name != "nt", reason="NTFS hardlinks")
class TestItRefusesNamesThatAreNotWhatTheyLookLike:
    """Shredding overwrites before it unlinks, which makes the name matter.

    A junction was guarded from the start. A HARDLINK was not, and it slips
    past that guard entirely: it is not a reparse point, it is a second
    directory entry on the same inode. So a file called app.db.plain.bak-999,
    hardlinked to somebody's notes, had the notes overwritten with random
    bytes while the notes file itself stayed right where it was. Creating a
    hardlink needs no privilege. Reproduced before this class existed.
    """

    def test_a_hardlinked_name_is_left_alone(self, client) -> None:
        folder = Path(config.DB_PATH).parent
        victim = folder / "someones_notes.txt"
        victim.write_text("MUST SURVIVE", encoding="utf-8")
        decoy = Path(config.DB_PATH).with_name(
            Path(config.DB_PATH).name + ".plain.bak-20260303140000")
        os.link(victim, decoy)
        try:
            body = client.post("/api/v1/vault/discard-plaintext-backup").json()

            assert victim.read_text(encoding="utf-8") == "MUST SURVIVE"
            assert body["removed"] == 0
            assert body["left"] == [decoy.name]
        finally:
            decoy.unlink(missing_ok=True)
            victim.unlink(missing_ok=True)

    def test_a_real_backup_beside_it_still_goes(self, client) -> None:
        # Refusing the trap must not turn into refusing to work.
        folder = Path(config.DB_PATH).parent
        victim = folder / "someones_notes.txt"
        victim.write_text("MUST SURVIVE", encoding="utf-8")
        decoy = Path(config.DB_PATH).with_name(
            Path(config.DB_PATH).name + ".plain.bak-20260303140000")
        os.link(victim, decoy)
        real = _backup("20260101120000")
        try:
            client.post("/api/v1/vault/discard-plaintext-backup")

            assert not real.exists(), "the genuine plaintext backup survived"
            assert victim.read_text(encoding="utf-8") == "MUST SURVIVE"
        finally:
            decoy.unlink(missing_ok=True)
            victim.unlink(missing_ok=True)


class TestItOnlyMatchesTheNameMigrationActuallyWrites:
    def test_the_separator_is_part_of_the_pattern(self, client) -> None:
        # Migration writes "<db>.plain.bak-<ts>". Widening the glob to
        # ".plain.bak*" kept every test green while pulling in names that
        # migration never produces - and this route SHREDS what it matches.
        live = Path(config.DB_PATH)
        stranger = live.with_name(live.name + ".plain.bakXYZ")
        stranger.write_bytes(b"not a migration artefact")
        try:
            assert client.get(
                "/api/v1/vault/status").json()["plaintext_backups"] == []
            client.post("/api/v1/vault/discard-plaintext-backup")
            assert stranger.exists(), "a name migration never writes was shredded"
        finally:
            stranger.unlink(missing_ok=True)

    def test_it_works_while_the_vault_is_locked(self, client) -> None:
        # Deliberate: the file is readable WITHOUT the passphrase, so demanding
        # one to remove it protects nothing and strands it for anyone who
        # forgot theirs. Nothing tested that, so a stray unlock requirement
        # would have been a silent regression.
        import vault_state

        backup = _backup()
        vault_state.clear_key()
        try:
            assert not vault_state.is_unlocked()
            body = client.post("/api/v1/vault/discard-plaintext-backup").json()
            assert body == {"removed": 1, "left": []}
            assert not backup.exists()
        finally:
            vault_state.set_key(conftest_key())


def conftest_key() -> bytes:
    from tests.conftest import TEST_VAULT_KEY
    return TEST_VAULT_KEY
