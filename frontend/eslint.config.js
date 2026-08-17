import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'
import { TEST_TREE } from './privacy-scope.js'

// Privacy rules, added in KADEME 21.
//
// These do NOT replace static-safety.test.ts. That file is the gate; this is
// the earlier line - it puts a red squiggle under the line while it is being
// typed, instead of on the next test run. Both stay.
//
// Three things the plan warned about, each measured before it was written:
//
// 1. SCOPE. static-safety.test.ts resolves its root to `frontend/`, so it
//    covers vite.config.ts today. Scoping these rules to `src/**` alone would
//    silently drop the single worst file to lose: a proxy target, a CDN, an
//    analytics plugin or a `define:`-injected secret all live there, and it
//    runs at BUILD time, so no CSP covers it in any build. It gets its own
//    block below.
// 2. OVERRIDES REPLACE, THEY DO NOT APPEND. A later block naming
//    `no-restricted-syntax` resets the whole array for those files. That is
//    why there is exactly one such block here, for tests, and it turns the
//    rules OFF rather than restating a subset - a restated subset is how an
//    entry point quietly loses every rule it was not re-given.
// 3. The secret's NAME stays banned, not only the mechanism that reads it.
//
// The test tree is exempt on purpose and not by accident: static-safety.test.ts
// must contain every literal it forbids in order to search for it, and the
// mock/fixture files carry sample payloads. Nothing under src/test ships.
// WHICH files that means is decided in privacy-scope.js, by name rather than
// by folder, and static-safety.test.ts reads the same answer.
//
// 4. WHAT THIS CANNOT SEE: `.html` and `.css`. No `files` pattern below
//    matches them, and a flat config skips an unmatched file in silence -
//    ESLint says "File ignored because no matching configuration was
//    supplied" and exits 0, which reads exactly like a pass. Covering them
//    means an HTML processor plugin, and a new dependency is the owner's call,
//    so the decision is written here instead of left implied: index.html and
//    index.css are covered by static-safety.test.ts (S-05, S-06, S-23), which
//    reads them as text, and that is the only line on them. If a second .css
//    or .html file ever appears, that gate is where it has to be added.

const RESTRICTED_GLOBALS = [
  { name: 'localStorage', message: 'S-09: device storage is for the ui store only, through its persist layer.' },
  { name: 'sessionStorage', message: 'S-04: nothing may be written to session storage.' },
  { name: 'indexedDB', message: 'S-13: no client-side database.' },
  { name: 'caches', message: 'S-17: no cache storage.' },
]

// no-restricted-globals only fires on an UNRESOLVED identifier, so
// `window.localStorage` and `globalThis.localStorage` walk straight past it.
// Both rules are needed; neither is redundant.
const RESTRICTED_PROPERTIES = [
  ...['window', 'globalThis', 'self'].flatMap((obj) => [
    { object: obj, property: 'localStorage', message: 'S-09: device storage is for the ui store only.' },
    { object: obj, property: 'sessionStorage', message: 'S-04: nothing may be written to session storage.' },
    { object: obj, property: 'indexedDB', message: 'S-13: no client-side database.' },
    { object: obj, property: 'caches', message: 'S-17: no cache storage.' },
  ]),
  { object: 'document', property: 'cookie', message: 'S-14: no cookies.' },
  { object: 'navigator', property: 'sendBeacon', message: 'S-15: nothing is beaconed anywhere.' },
  { object: 'navigator', property: 'serviceWorker', message: 'S-16: no service worker.' },
]

const RESTRICTED_SYNTAX = [
  {
    selector: 'Literal[value=/openrouter\\.ai/i]',
    message: 'S-01: the provider host belongs to the backend. The frontend talks to 127.0.0.1 only.',
  },
  {
    selector: 'TemplateElement[value.raw=/openrouter\\.ai/i]',
    message: 'S-01: the provider host belongs to the backend, in template strings too.',
  },
  {
    selector: 'Literal[value=/^(authorization|bearer )/i]',
    message: 'S-11b: the frontend never constructs an auth header. The key never reaches it.',
  },
  {
    selector: 'JSXAttribute[name.name="dangerouslySetInnerHTML"]',
    message: 'S-18: message text is rendered as React nodes, never as an HTML string.',
  },
  {
    selector: 'Property[key.name="dangerouslySetInnerHTML"]',
    message: 'S-18: message text is rendered as React nodes, never as an HTML string.',
  },
  {
    // The NAME of the secret, not just the mechanism that would read it.
    // Banning `import.meta.env` alone leaves the name free wherever the
    // mechanism happens not to be used.
    selector: 'Literal[value=/OPENROUTER_API_KEY|VITE_OPENROUTER|VITE_API_KEY/]',
    message: 'S-19: the key has no name on this side because it has no presence on this side.',
  },
  {
    selector: 'TemplateElement[value.raw=/OPENROUTER_API_KEY|VITE_OPENROUTER|VITE_API_KEY/]',
    message: 'S-19: the key has no name on this side, in template strings either.',
  },
  // The object-literal form was all this saw for a while. `payload.zdr = false`
  // and `payload['data_collection'] = true` are the same instruction written
  // the second most obvious way, and both were measured walking straight past.
  // Two independent adversarial passes found this separately, which is a fair
  // sign of how reachable it is.
  ...['zdr', 'data_collection', 'allow_fallbacks'].flatMap((field) => [
    {
      selector: `Property[key.name="${field}"], Property[key.value="${field}"]`,
      message: `S-20: provider routing (${field}) is the backend's single decision, not a field the client sends.`,
    },
    {
      selector: `MemberExpression[property.name="${field}"], MemberExpression[property.value="${field}"]`,
      message: `S-20: provider routing (${field}) is the backend's decision, and assigning it after the fact is the same instruction.`,
    },
  ]),
  {
    // Case-insensitive, like the openrouter.ai selectors above. It was not,
    // and `data:image/png;BASE64,` was measured going past - one flag away
    // from the thing S-21 exists to stop.
    selector: 'Literal[value=/;base64/i]',
    message: 'S-21: the frontend does not build multimodal payloads.',
  },
  {
    // Without this, `data:${mime};base64,${b64}` slips through: the literal
    // ban only sees whole string literals, and a template splits at every
    // interpolation.
    selector: 'TemplateElement[value.raw=/;base64/i]',
    message: 'S-21: the frontend does not build multimodal payloads, in template strings either.',
  },
]

const PRIVACY_RULES = {
  'no-console': ['error'],
  'no-restricted-globals': ['error', ...RESTRICTED_GLOBALS],
  'no-restricted-properties': ['error', ...RESTRICTED_PROPERTIES],
  'no-restricted-syntax': ['error', ...RESTRICTED_SYNTAX],
}

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    rules: PRIVACY_RULES,
  },
  {
    // Correction 1: the build config is covered too, and by the same rules.
    files: ['vite.config.ts', '*.config.ts'],
    languageOptions: { globals: globals.node },
    rules: PRIVACY_RULES,
  },
  {
    // The same two exemptions static-safety.test.ts already grants, named
    // here so the two gates cannot disagree. Found by running the rules:
    // written without these, ESLint was STRICTER than the gate it mirrors,
    // which is its own kind of wrong - a rule nobody can satisfy gets
    // switched off wholesale by the next person.
    //
    // chatBgDb.ts is S-13's approved blob store: the user's chosen wallpaper
    // lives there as a Blob, deliberately out of localStorage to avoid the
    // data-URI size trap. Decorative preference data, never conversation.
    files: ['src/lib/store/chatBgDb.ts'],
    rules: { 'no-restricted-globals': 'off', 'no-restricted-properties': 'off' },
  },
  {
    // narrationMigration.ts READS the legacy ui-state blob once, to move a
    // setting off the device and into the vault. It never writes there.
    files: ['src/lib/voice/narrationMigration.ts'],
    rules: { 'no-restricted-properties': 'off' },
  },
  {
    // Correction 2: turned OFF wholesale, not restated as a narrower list.
    // These files exist to contain the forbidden patterns.
    //
    // Correction 3, 2026-08-17: this used to read `src/test/**/*.{ts,tsx}`,
    // which asked where a file sits instead of what it is. It now comes from
    // privacy-scope.js, which static-safety.test.ts reads as well, so the two
    // gates cannot drift apart and a file dropped into the test tree under an
    // ordinary name is exempt from neither.
    files: TEST_TREE,
    rules: {
      'no-console': 'off',
      'no-restricted-globals': 'off',
      'no-restricted-properties': 'off',
      'no-restricted-syntax': 'off',
    },
  },
])
