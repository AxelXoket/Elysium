/**
 * Seeds the device-local blob that `narrationMigration` has to move.
 *
 * Lives under test/mocks because static-safety S-09 forbids
 * `window.localStorage` writes outside `lib/store`, and it is right to: a
 * setting the server also reads must not have a second home. Seeding the
 * legacy shape - which the store no longer produces - is the one case that
 * cannot go through the store, so it uses the exemption that already exists
 * rather than widening the rule.
 */
const UI_STATE_KEY = "elysium-ui-state";

export function seedDeviceNarration(mode: string | null): void {
  const state: Record<string, unknown> = { continuousVoice: false };
  if (mode !== null) state.narrationVoice = mode;
  window.localStorage.setItem(UI_STATE_KEY, JSON.stringify({ state, version: 0 }));
}

export function seedRawUiState(raw: string): void {
  window.localStorage.setItem(UI_STATE_KEY, raw);
}
