"""FAZ 3 - the capture-exclusion switch, and the transition it hangs on.

The Win32 half already existed and was gated behind an environment variable.
What FAZ 3 changes is who decides and when: the setting moves into the vault,
and it is applied on LOCK TRANSITIONS rather than at launch.

That is not a preference about where to put a call. `harden()` runs before the
server starts and long before a passphrase has been entered, so a setting
stored inside the vault is literally unreadable there. The owner's rule -
"while the app is locked, do not apply it either way" - is what makes that
solvable rather than a contradiction: protection belongs on only while a
conversation is on screen.
"""
from __future__ import annotations

import database
from routers import vault as vault_router


class TestTheSettingFailsClosed:
    def test_absent_means_off(self, db) -> None:
        """A protection setting that defaults to ON when it cannot be read
        would black out the window of somebody who never asked for it, with
        nothing on screen explaining why."""
        assert vault_router.screen_privacy_enabled() is False

    def test_a_locked_vault_reads_as_off_rather_than_raising(self, db) -> None:
        import vault_state
        database.set_setting(vault_router.SETTING_SCREEN_PRIVACY, "1")
        key = vault_state.get_key()
        vault_state.clear_key()
        try:
            assert vault_router.screen_privacy_enabled() is False
        finally:
            vault_state.set_key(key)

    def test_on_is_read_back(self, db) -> None:
        database.set_setting(vault_router.SETTING_SCREEN_PRIVACY, "1")
        assert vault_router.screen_privacy_enabled() is True


class TestItIsAStateTransitionNotASetting:
    """§G-16. The failure this guards is asymmetric and both halves are real:
    leaving it ON while locked blacks out a window with nothing to hide, and
    leaving it OFF after unlock is the switch silently not applying."""

    def _record(self, monkeypatch) -> list[bool]:
        calls: list[bool] = []
        import win_hardening
        monkeypatch.setattr(win_hardening, "set_screen_privacy",
                            lambda enabled: calls.append(enabled) or 0)
        return calls

    def test_unlocking_with_the_switch_on_applies_it(self, db, monkeypatch):
        calls = self._record(monkeypatch)
        database.set_setting(vault_router.SETTING_SCREEN_PRIVACY, "1")
        vault_router._apply_screen_privacy(True)
        assert calls == [True]

    def test_locking_removes_it_even_with_the_switch_on(self, db, monkeypatch):
        """The owner's rule, and the reason the launch-order problem is not a
        problem: there is nothing on a locked screen but a masked passphrase
        box."""
        calls = self._record(monkeypatch)
        database.set_setting(vault_router.SETTING_SCREEN_PRIVACY, "1")
        vault_router._apply_screen_privacy(False)
        assert calls == [False]

    def test_unlocking_with_the_switch_off_does_not_apply_it(
        self, db, monkeypatch
    ) -> None:
        calls = self._record(monkeypatch)
        vault_router._apply_screen_privacy(True)
        assert calls == [False]

    def test_it_never_raises_into_a_route(self, db, monkeypatch) -> None:
        """A build with no window, an OS that does not have the API, a call
        that fails - none of those may take the vault down with them."""
        import win_hardening

        def boom(enabled):
            raise OSError("no window here")

        monkeypatch.setattr(win_hardening, "set_screen_privacy", boom)
        database.set_setting(vault_router.SETTING_SCREEN_PRIVACY, "1")
        vault_router._apply_screen_privacy(True)      # must not raise


class TestTheRouteAppliesImmediately:
    def test_turning_it_on_takes_effect_without_relocking(
        self, client, db, monkeypatch
    ) -> None:
        """A switch that only takes effect on the next unlock is one the user
        cannot tell worked."""
        calls: list[bool] = []
        import win_hardening
        monkeypatch.setattr(win_hardening, "set_screen_privacy",
                            lambda enabled: calls.append(enabled) or 0)

        r = client.post("/api/v1/settings/screen-privacy",
                        json={"screen_privacy_enabled": True})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "screen_privacy_enabled": True}
        assert calls == [True], "stored but not applied"

    def test_it_is_reported_by_the_settings_read(self, client, db) -> None:
        client.post("/api/v1/settings/screen-privacy",
                    json={"screen_privacy_enabled": True})
        assert client.get("/api/v1/settings").json()[
            "screen_privacy_enabled"] is True
