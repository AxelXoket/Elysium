"""The floor was eight characters, and there is no login box behind it.

Everything protecting this vault reduces to one secret, and an attacker who
copies the folder guesses at it offline with no rate limit at all. scrypt sets
the price per guess; the passphrase sets how many guesses are needed. An
8-character lowercase passphrase is about 2^37 candidates - weeks on one
machine, hours on rented hardware - and the app accepted it.

What is NOT tested here, because it is not implemented: composition rules. A
required capital, digit and symbol is what produces "Password1!", and it makes
the space people actually choose from smaller while looking like it makes it
bigger.
"""
from __future__ import annotations

import pytest

import passphrase_strength as strength


class TestLength:
    def test_the_floor_is_high_enough_to_matter(self) -> None:
        assert strength.MIN_PASSPHRASE_LEN >= 12

    @pytest.mark.parametrize("passphrase", [
        "", "a", "short", "eightchr", "elevenchar",
    ])
    def test_a_short_one_is_refused(self, passphrase: str) -> None:
        assert strength.assess(passphrase) == "passphrase_too_short"

    def test_an_absurdly_long_one_is_refused(self) -> None:
        assert strength.assess("x" * 5000) == "passphrase_too_long"

    def test_the_length_that_used_to_be_the_floor_is_now_below_it(self
                                                                  ) -> None:
        # The regression this file exists to prevent: somebody lowering the
        # constant back to 8 for convenience.
        assert strength.assess("Tr0ub4dr") == "passphrase_too_short"


class TestShapesThatAreLongButNotSecret:
    @pytest.mark.parametrize("passphrase", [
        "aaaaaaaaaaaaaa",
        "abababababababab",
        "passpasspasspass",
        "xyzxyzxyzxyzxyz",
    ])
    def test_one_idea_typed_repeatedly_is_refused(self, passphrase: str
                                                  ) -> None:
        assert strength.assess(passphrase) == "passphrase_too_simple"

    @pytest.mark.parametrize("passphrase", [
        "abcdefghijklm",
        "qwertyuiopasd",
        "0987654321098",
        "mnbvcxzlkjhgf",
    ])
    def test_a_walk_along_the_keyboard_is_refused(self, passphrase: str
                                                  ) -> None:
        assert strength.assess(passphrase) == "passphrase_too_simple"

    @pytest.mark.parametrize("passphrase", [
        "aaaaaaaabcde",      # a long run plus a short tail
        "xxxxxxxxxxhello",
        "1111111111abcd",
    ])
    def test_mostly_one_character_plus_filler_is_refused(
        self, passphrase: str
    ) -> None:
        # This cleared every other rule: long enough, five distinct
        # characters, not a full repetition, not a full keyboard walk. And
        # "one character repeated, then a short tail" is among the first masks
        # any cracking tool tries.
        assert strength.assess(passphrase) == "passphrase_too_simple"

    def test_an_ordinary_phrase_with_repeated_letters_is_still_fine(
        self
    ) -> None:
        # The control the share threshold has to leave alone: real text
        # repeats spaces and vowels constantly.
        assert strength.assess("the little green lantern") is None

    def test_too_few_distinct_characters_is_refused(self) -> None:
        assert strength.assess("abcabcabcabcab") == "passphrase_too_simple"

    @pytest.mark.parametrize("passphrase", [
        "xzyxzxyzxzyx",   # 4 distinct, no run, no repeat unit, no dominant char
        "qzqwqzwqzqzw",   # 4 distinct, arranged so no other rule fires either
    ])
    def test_few_distinct_characters_with_no_other_signature_is_refused(
        self, passphrase: str,
    ) -> None:
        """The distinct-character floor, on its own.

        The case above it ("abcabcabcabcab") is ALSO a repeated unit, so it
        stays refused even if MIN_DISTINCT_CHARS is lowered - measured: with
        the floor dropped to 3, every literal in this file still failed for
        some other reason, so the constant had no test that could see it move.
        These two trip nothing except the distinct count.
        """
        assert strength.assess(passphrase) == "passphrase_too_simple"

    @pytest.mark.parametrize("passphrase", [
        "password1234", "iloveyou1234", "qwertyuiopas",
        "correcthorsebatterystaple",
    ])
    def test_a_well_known_one_is_refused(self, passphrase: str) -> None:
        assert strength.assess(passphrase) == "passphrase_too_common"

    def test_case_does_not_hide_a_known_one(self) -> None:
        assert strength.assess("PassWord1234") == "passphrase_too_common"

    def test_spacing_does_not_hide_a_known_one(self) -> None:
        assert strength.assess("password 1234") == "passphrase_too_common"


class TestWhatMustStillBeAccepted:
    """A gate that refuses good passphrases pushes people to worse ones."""

    @pytest.mark.parametrize("passphrase", [
        "seaside orchid harbour",
        "the-quiet-lamp-by-the-door",
        "Tr0ub4dor&3xkcd",
        "kirmizi bisiklet yagmurda",     # not English, and it should not care
        "\U0001f300 spiral lantern 47",  # nor should it care about scripts
    ])
    def test_a_real_passphrase_is_accepted(self, passphrase: str) -> None:
        assert strength.assess(passphrase) is None

    def test_a_passphrase_with_no_digits_or_symbols_is_fine(self) -> None:
        # The composition rule this deliberately does not have.
        assert strength.assess("many quiet lanterns burning") is None


class TestTheRoutesEnforceIt:
    def _fresh_vault(self, client, tmp_path, monkeypatch):
        import config
        import database
        import vault_state

        vdir = tmp_path / "gate"
        vdir.mkdir()
        db_path = str(vdir / "app.db")
        monkeypatch.setattr(config, "DB_PATH", db_path)
        monkeypatch.setattr(database, "DB_PATH", db_path)
        vault_state.clear_key()

    @pytest.mark.parametrize("passphrase, reason", [
        ("shortish", "passphrase_too_short"),
        ("password1234", "passphrase_too_common"),
        ("abababababab", "passphrase_too_simple"),
    ])
    def test_init_refuses_and_says_why(
        self, client, tmp_path, monkeypatch: pytest.MonkeyPatch,
        passphrase: str, reason: str,
    ) -> None:
        # The code, not a sentence: the wording belongs to the frontend, and a
        # test asserting the sentence is a copy of the sentence.
        self._fresh_vault(client, tmp_path, monkeypatch)
        response = client.post("/api/v1/vault/init",
                               json={"passphrase": passphrase})
        assert response.status_code == 422
        assert response.json()["detail"] == reason

    def test_init_accepts_a_real_one(
        self, client, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._fresh_vault(client, tmp_path, monkeypatch)
        assert client.post("/api/v1/vault/init",
                           json={"passphrase": "seaside orchid harbour"}
                           ).status_code == 200

    def test_changing_to_a_weak_one_is_refused(
        self, client, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._fresh_vault(client, tmp_path, monkeypatch)
        client.post("/api/v1/vault/init",
                    json={"passphrase": "seaside orchid harbour"})
        response = client.post("/api/v1/vault/change-passphrase", json={
            "old_passphrase": "seaside orchid harbour",
            "new_passphrase": "aaaaaaaaaaaaaa",
        })
        assert response.status_code == 422
        assert response.json()["detail"] == "passphrase_too_simple"

    def test_an_existing_weak_passphrase_still_unlocks(
        self, client, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # THE compatibility property. The gate is on SETTING a passphrase, so
        # raising the bar must not lock out somebody who already has a vault
        # behind an 8-character one - they meet the new bar when they next
        # change it, on their own schedule.
        import config
        import crypto
        import database
        import vault_state

        vdir = tmp_path / "legacy"
        vdir.mkdir()
        db_path = vdir / "app.db"
        monkeypatch.setattr(config, "DB_PATH", str(db_path))
        monkeypatch.setattr(database, "DB_PATH", str(db_path))
        vault_state.clear_key()

        vault = crypto.KeyVault(vdir)
        key = vault.initialize("old8char")     # below today's floor
        vault_state.set_key(key)
        database.init_db()
        vault_state.clear_key()

        response = client.post("/api/v1/vault/unlock",
                               json={"passphrase": "old8char"})
        assert response.status_code == 200, response.text
