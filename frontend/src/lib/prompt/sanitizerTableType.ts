/** The shape of shared/prompt_sanitizer.json (see backend/prompt_sanitizer.py). */

export type SanitizerRange = readonly [number, number];
export type SanitizerFoldEntry = readonly (number | string)[];

export interface SanitizerTable {
  _why: string;
  format: number;
  rules_version: number;
  unicode_version: string;
  subdivision_flags: readonly string[];
  space: readonly SanitizerRange[];
  invisible: readonly SanitizerRange[];
  unassigned_ignorable: readonly SanitizerRange[];
  han: readonly SanitizerRange[];
  name_strip: readonly SanitizerRange[];
  hangul: boolean;
  fold: readonly SanitizerFoldEntry[];
}
