// Which files the privacy gates treat as test scaffolding, written once.
//
// Two gates need this answer and they used to answer it separately:
// eslint.config.js exempted `src/test/**`, and static-safety.test.ts skipped
// anything with a `test` directory segment in its path. Both asked WHERE a
// file sits rather than WHAT it is, so a single misplaced file switched both
// of them off at once. Measured on 2026-08-16: a production-shaped function
// carrying four violations - a console.log naming a secret, localStorage, the
// provider host and a Bearer literal - was dropped into src/test/ and `eslint`
// printed nothing and exited 0.
//
// The answer here is a file's NAME, plus a short list of exact paths for the
// scaffolding that cannot carry a `.test.` name. The list is exact paths and
// not a glob on purpose: a new file appearing beside these gets no exemption
// until somebody writes it down here, and writing it down is the reviewable
// act that was missing.

/** Support files that are not tests but exist to serve them. Exact paths. */
export const SUPPORT_FILES = [
  // Vitest's own entry point, named by vitest.config.ts.
  'src/test/setup.ts',
  'src/test/helpers/clipboardMock.ts',
  'src/test/helpers/fakeAudioContext.ts',
  'src/test/helpers/renderWithQueryClient.tsx',
  'src/test/helpers/streamMocks.ts',
  // These carry sample payloads, which is the whole reason they exist and
  // also the reason they would trip several rules.
  'src/test/mocks/api.ts',
  'src/test/mocks/fixtures.ts',
  'src/test/mocks/legacyStorage.ts',
]

/** What ESLint exempts: a test by its name, or one of the files above. */
export const TEST_TREE = ['**/*.test.{ts,tsx}', ...SUPPORT_FILES]

/** True when a repo-relative path, POSIX separators, is scaffolding. */
export function isScaffolding(relativePath) {
  const posix = relativePath.split('\\').join('/')
  return /\.test\.(ts|tsx)$/.test(posix) || SUPPORT_FILES.includes(posix)
}
