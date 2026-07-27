/**
 * parseServerDate / serverDateTimeAttr (v1.1 FF2 + H6).
 * Backend zone-less UTC strings must render as local time; optimistic ISO
 * strings must NOT get a second "Z" (which yields Invalid Date).
 */
import { describe, it, expect } from "vitest";
import { parseServerDate, serverDateTimeAttr } from "@/lib/chat/serverDate";

describe("parseServerDate", () => {
  it("treats a zone-less backend string as UTC", () => {
    const d = parseServerDate("2026-07-19 09:30:00");
    // Interpreted as UTC -> epoch matches the explicit Z form.
    expect(d.getTime()).toBe(Date.parse("2026-07-19T09:30:00Z"));
    expect(Number.isNaN(d.getTime())).toBe(false);
  });

  it("does NOT double-suffix an ISO string with Z (optimistic rows)", () => {
    const iso = "2026-07-19T09:30:00.000Z";
    const d = parseServerDate(iso);
    expect(Number.isNaN(d.getTime())).toBe(false);
    expect(d.getTime()).toBe(Date.parse(iso));
  });

  it("respects an explicit numeric offset", () => {
    const withOffset = "2026-07-19T12:30:00+03:00";
    const d = parseServerDate(withOffset);
    expect(Number.isNaN(d.getTime())).toBe(false);
    expect(d.getTime()).toBe(Date.parse(withOffset));
  });

  it("does not shift by the local offset (regression: was 3h off)", () => {
    // A UTC 00:00 must be the same instant regardless of the runner's TZ.
    const d = parseServerDate("2026-07-19 00:00:00");
    expect(d.toISOString()).toBe("2026-07-19T00:00:00.000Z");
  });
});

describe("serverDateTimeAttr", () => {
  it("returns a valid ISO string for a zone-less input", () => {
    expect(serverDateTimeAttr("2026-07-19 09:30:00")).toBe(
      "2026-07-19T09:30:00.000Z",
    );
  });

  it("falls back to the raw string if unparseable", () => {
    expect(serverDateTimeAttr("not-a-date")).toBe("not-a-date");
  });
});
