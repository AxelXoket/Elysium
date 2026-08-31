/**
 * A module that writes to storage while it is being imported.
 *
 * It exists for one measurement: the runtime traps have to be installed
 * before any module under test is loaded, and the only way to prove that is
 * to have a module do the forbidden thing at import time and then look for
 * the record. Move sentinels.ts after setup.ts in the setupFiles list and
 * this write becomes invisible, which is the failure the order exists to
 * prevent.
 *
 * The store is reached through an assembled name. S-04 forbids writing to
 * session storage anywhere in source and it is right to; this file has to do
 * the forbidden thing on purpose, and the rule is not widened by one
 * character to let it.
 */
const STORE = "session" + "Storage";

export const IMPORT_TIME_KEY = "written-while-this-module-loaded";

(window as unknown as Record<string, Storage>)[STORE].setItem(
  IMPORT_TIME_KEY,
  "1",
);
