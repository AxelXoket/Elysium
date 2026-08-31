"""U-76 - the half of the invariant block that was still true, now guarded.

`routers/settings.py` opens with a "Privacy invariants" list, and two of its
four claims were the opposite of the truth: it said the API key is never
stored in SQLite (it is, in `vault_secrets`, encrypted by SQLCipher) and that
the module never calls OpenRouter (it does - `validate_api_key` sends the key
itself). Both had been wrong since the move off the OS keyring.

The text is corrected. This file guards the half that was ALWAYS true and
that nothing was reading, because that is the half a future edit could break
without anybody noticing: the key is never RETURNED and never LOGGED.
"""
from __future__ import annotations

import logging

import config
import secrets_service
from database import get_db


class TestTheKeyIsStoredButNeverHandedBack:
    def test_settings_answers_with_a_boolean_not_the_key(self, client) -> None:
        secret = "sk-or-v1-a-very-secret-value"

        # The shared fixture seeds a key so the notebook worker has one, so
        # the "none stored yet" state has to be made rather than assumed.
        with get_db() as con:
            con.execute("DELETE FROM vault_secrets WHERE name = ?",
                        (config.SECRET_API_KEY,))

        before = client.get("/api/v1/settings")
        assert before.status_code == 200
        assert before.json()["api_key_set"] is False, "ground: none stored yet"

        secrets_service.set_secret(config.SECRET_API_KEY, secret)

        after = client.get("/api/v1/settings")
        assert after.json()["api_key_set"] is True
        assert secret not in after.text, "the key came back over the wire"

    def test_it_really_is_in_the_vault(self, client) -> None:
        """The claim the docstring got backwards, stated as behaviour.

        Not to demand that it be stored - that is E5's deliberate design -
        but so the invariant block can never drift back to saying it is not.
        """
        secret = "sk-or-v1-stored-in-the-vault"
        secrets_service.set_secret(config.SECRET_API_KEY, secret)

        with get_db() as con:
            row = con.execute(
                "SELECT value FROM vault_secrets WHERE name = ?",
                (config.SECRET_API_KEY,)).fetchone()

        assert row is not None, "the key was not stored at all"
        assert row["value"] == secret

    def test_the_key_ROUTES_write_nothing_of_it_to_the_log(
            self, client, caplog, monkeypatch) -> None:
        """The other half that was always true, driven through the routes
        that actually speak.

        AN EARLIER VERSION OF THIS TEST WAS VACUOUS. It called
        `secrets_service.set_secret` directly and read `GET /settings`, and
        neither of those logs anything on the happy path - measured: zero
        records. The absence was asserted over an empty string, and the
        "positive control" beside it emitted its own line through the
        module's logger name, proving the caplog plumbing worked rather than
        that the module writes.

        These are the three routes that DO log about the key
        (`settings.py:345`, `:380`, `:462`). The key travels through all of
        them and must appear in none.
        """
        import openrouter

        secret = "sk-or-v1-must-not-appear-in-any-log-line"

        async def accepted(*a, **kw):
            return "valid"

        monkeypatch.setattr(openrouter, "validate_api_key", accepted)

        with caplog.at_level(logging.DEBUG):
            client.post("/api/v1/settings/api-key", json={"api_key": secret})
            client.post("/api/v1/settings/api-key/check")
            client.delete("/api/v1/settings/api-key")

        # POSITIVE CONTROL, and it is the module's OWN words this time: if
        # these sentences are absent the routes did not run and the absence
        # below means nothing.
        assert "API key validated and saved." in caplog.text
        assert "API key deleted." in caplog.text

        assert secret not in caplog.text
        for fragment in (secret[:20], secret[-20:]):
            assert fragment not in caplog.text, "a slice of the key was logged"

    def test_the_key_is_really_what_travelled(
            self, client, monkeypatch) -> None:
        """GROUND for the test above: the routes were handed the real secret,
        not a placeholder that could never have leaked."""
        import openrouter

        secret = "sk-or-v1-this-exact-value-was-sent"
        seen: list[str] = []

        async def capture(key, *a, **kw):
            seen.append(key)
            return "valid"

        monkeypatch.setattr(openrouter, "validate_api_key", capture)
        client.post("/api/v1/settings/api-key", json={"api_key": secret})

        assert seen == [secret]
