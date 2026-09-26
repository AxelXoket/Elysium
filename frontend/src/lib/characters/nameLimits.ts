/**
 * The name cap for characters and personas (Ek P, B8), in code points.
 *
 * The source of truth is shared/name_limits.json, which the backend reads.
 * App code cannot import from shared/ (the dev server only serves frontend/),
 * so this is a mirror and nameLimits.test.ts holds it equal to the file.
 */
export const NAME_MAX_CODE_POINTS = 64;
