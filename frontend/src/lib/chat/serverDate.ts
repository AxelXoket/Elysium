/**
 * serverDate - parse a backend timestamp into a correct local Date.
 *
 * The backend stores SQLite `datetime('now')` as a zone-less UTC string
 * ("YYYY-MM-DD HH:MM:SS"); Chromium's `new Date("2026-01-01 12:00:00")`
 * parses a space-separated datetime as LOCAL time, shifting every displayed
 * time by the UTC offset. This normalizes the space to `T` and appends `Z`
 * so it is unambiguously UTC.
 *
 * Optimistic rows already carry a full ISO string (`new Date().toISOString()`,
 * e.g. "...Z" or an offset), so we must NOT append a second `Z` - that yields
 * "Invalid Date". The guard skips normalization when a zone marker is present.
 * (v1.1 FF2 + review H6.)
 */

const HAS_ZONE = /([zZ]|[+-]\d{2}:?\d{2})$/;

export function parseServerDate(ts: string): Date {
  if (HAS_ZONE.test(ts.trim())) return new Date(ts);
  return new Date(ts.replace(" ", "T") + "Z");
}

/** ISO-8601 form suitable for a `<time dateTime>` attribute. */
export function serverDateTimeAttr(ts: string): string {
  const d = parseServerDate(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toISOString();
}
