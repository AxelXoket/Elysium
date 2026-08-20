"""v1.2 privacy fix: the selected model id moves out of localStorage.

`selectedModelId` used to be one of three keys in uiStore's `elysium-ui-state`
blob, written to WebView2's on-disk localStorage in the clear. Two of those
keys (selectedChatId, selectedCharacterId) are bare numbers, which the
owner's own rule permits to stay device-local. The third is not: an
OpenRouter model id such as "anthropic/claude-3.5-sonnet" is a NAME a person
reads on screen, and browser_profile.purge() deliberately spares Local
Storage - so that name survived every lock, relaunch and shutdown, readable
with no passphrase.

This module is the vault-side half of the fix: POST /settings/model-selection
writes the value into the encrypted `settings` table (config.
SETTING_SELECTED_MODEL_ID), and GET /settings reads it back from there. The
frontend half - stripping the plaintext copy out of every existing install -
is uiStore.ts's version-3 `migrate`, proven in
frontend/src/test/settings-persistence.test.ts.
"""
from __future__ import annotations

import config
import database


class TestTheRouteRoundTrips:
    def test_setting_it_is_read_back(self, client) -> None:
        r = client.post(
            "/api/v1/settings/model-selection",
            json={"selected_model_id": "anthropic/claude-3.5-sonnet"},
        )
        assert r.status_code == 200
        assert r.json() == {
            "ok": True,
            "selected_model_id": "anthropic/claude-3.5-sonnet",
        }
        assert (
            client.get("/api/v1/settings").json()["selected_model_id"]
            == "anthropic/claude-3.5-sonnet"
        )

    def test_nothing_chosen_yet_reads_as_null(self, client) -> None:
        # GROUND: a fresh vault, before this endpoint is ever called, must
        # not already read as some value - otherwise every other assertion
        # in this file proves nothing about what the endpoint itself does.
        assert client.get("/api/v1/settings").json()["selected_model_id"] is None

    def test_clearing_it_writes_null_not_the_old_value(self, client) -> None:
        client.post(
            "/api/v1/settings/model-selection",
            json={"selected_model_id": "anthropic/claude-3.5-sonnet"},
        )
        r = client.post("/api/v1/settings/model-selection", json={})
        assert r.status_code == 200
        assert r.json()["selected_model_id"] is None
        assert client.get("/api/v1/settings").json()["selected_model_id"] is None

    def test_a_misspelt_field_is_refused_not_treated_as_a_clear(
        self, client
    ) -> None:
        """docs/frontend_contract.md named this field `model_id` until
        2026-08-20, and the body model used to ignore unknown fields. A client
        written from the published contract therefore sent a field nobody
        read, `selected_model_id` defaulted to None, and the endpoint DELETED
        the stored selection and answered `{"ok": true}` - a destructive write
        reported as a success. Absence means "clear" here, so `ignore` and
        `forbid` are not interchangeable on this route the way they are on the
        completions bodies."""
        client.post(
            "/api/v1/settings/model-selection",
            json={"selected_model_id": "anthropic/claude-3.5-sonnet"},
        )
        # GROUND: it really is stored, so the assertion below is about the
        # misspelt request and not about an empty setting.
        assert (
            client.get("/api/v1/settings").json()["selected_model_id"]
            == "anthropic/claude-3.5-sonnet"
        )

        r = client.post(
            "/api/v1/settings/model-selection",
            json={"model_id": "openai/gpt-4o"},
        )

        assert r.status_code == 422
        assert (
            client.get("/api/v1/settings").json()["selected_model_id"]
            == "anthropic/claude-3.5-sonnet"
        ), "a request nobody could read still wiped the stored selection"

    def test_an_empty_string_clears_it_too(self, client) -> None:
        # A stray "" from a client-side bug must behave exactly like an
        # explicit null - it is not itself a model id.
        client.post(
            "/api/v1/settings/model-selection",
            json={"selected_model_id": "anthropic/claude-3.5-sonnet"},
        )
        r = client.post("/api/v1/settings/model-selection",
                        json={"selected_model_id": ""})
        assert r.json()["selected_model_id"] is None

    def test_surrounding_whitespace_is_trimmed(self, client) -> None:
        r = client.post(
            "/api/v1/settings/model-selection",
            json={"selected_model_id": "  anthropic/claude-3.5-sonnet  "},
        )
        assert r.json()["selected_model_id"] == "anthropic/claude-3.5-sonnet"

    def test_saving_one_setting_does_not_disturb_another(self, client) -> None:
        client.post("/api/v1/settings/auto-lock", json={"auto_lock_minutes": 9})
        client.post(
            "/api/v1/settings/model-selection",
            json={"selected_model_id": "anthropic/claude-3.5-sonnet"},
        )
        body = client.get("/api/v1/settings").json()
        assert body["auto_lock_minutes"] == 9
        assert body["selected_model_id"] == "anthropic/claude-3.5-sonnet"


class TestTheDefensiveClamp:
    """UI state, not a security boundary: an oversized or malformed value is
    clamped rather than rejected with a 422, the same choice
    save_stop_sequences makes for its own list."""

    def test_an_absurdly_long_id_is_truncated_not_refused(self, client) -> None:
        huge = "vendor/" + ("x" * 400)
        r = client.post(
            "/api/v1/settings/model-selection",
            json={"selected_model_id": huge},
        )
        assert r.status_code == 200
        stored = r.json()["selected_model_id"]
        assert stored == huge[: config.SELECTED_MODEL_ID_MAX_CHARS]

    def test_the_ceiling_is_measured_not_assumed(self, client) -> None:
        # POSITIVE CONTROL: prove the clamp actually bites at the configured
        # length rather than merely returning something shorter than "huge".
        exactly_at_ceiling = "m" * config.SELECTED_MODEL_ID_MAX_CHARS
        one_over = exactly_at_ceiling + "x"

        kept = client.post(
            "/api/v1/settings/model-selection",
            json={"selected_model_id": exactly_at_ceiling},
        ).json()["selected_model_id"]
        assert kept == exactly_at_ceiling
        assert len(kept) == config.SELECTED_MODEL_ID_MAX_CHARS

        clamped = client.post(
            "/api/v1/settings/model-selection",
            json={"selected_model_id": one_over},
        ).json()["selected_model_id"]
        assert clamped == exactly_at_ceiling
        assert len(clamped) == config.SELECTED_MODEL_ID_MAX_CHARS


class TestTheValueLivesInTheVaultNotLocalstorage:
    def test_it_is_a_row_in_the_settings_table(self, client) -> None:
        # Confirms the storage location directly: the settings table is the
        # encrypted vault, never the browser. This is the guarantee the whole
        # fix rests on - a value here cannot be read while the vault is
        # locked, unlike a WebView2 localStorage blob.
        client.post(
            "/api/v1/settings/model-selection",
            json={"selected_model_id": "anthropic/claude-3.5-sonnet"},
        )
        assert (
            database.get_setting(config.SETTING_SELECTED_MODEL_ID)
            == "anthropic/claude-3.5-sonnet"
        )
