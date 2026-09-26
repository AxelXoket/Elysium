import { describe, expect, it } from "vitest";

import nameLimits from "../../../../shared/name_limits.json";
import { NAME_MAX_CODE_POINTS } from "@/lib/characters/nameLimits";
import { getErrorMessage } from "@/lib/errors/errorMessages";

describe("name cap mirror (Ek P, B8)", () => {
  it("equals the shared file the backend reads", () => {
    expect(nameLimits.name_max_code_points).toBeGreaterThan(0);
    expect(NAME_MAX_CODE_POINTS).toBe(nameLimits.name_max_code_points);
  });

  it("the refusal sentence names the cap", () => {
    const text = getErrorMessage("name_too_long");
    // Ground: a known code, not the fallback sentence.
    expect(text).not.toBe(getErrorMessage("no_such_code_ever"));
    expect(text).toContain(String(nameLimits.name_max_code_points));
  });
});
