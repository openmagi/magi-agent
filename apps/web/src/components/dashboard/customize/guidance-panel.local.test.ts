import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./guidance-panel.tsx", import.meta.url),
  "utf8",
);

describe("GuidancePanel — UX restructure B soft-instructions surface", () => {
  it("renders an amber warning banner so the reliability boundary is honest", () => {
    expect(src).toContain("amber-500/30");
    expect(src).toContain("Soft instructions");
    expect(src).toContain("is not");
    expect(src).toContain("deterministically forced");
  });

  it("points users to Presets / Gates for hard enforcement", () => {
    expect(src).toContain("Presets or");
    expect(src).toContain("Gates");
  });

  it("disables the save button until the draft diverges from the persisted value", () => {
    expect(src).toContain("disabled={!dirty || rulesSaving}");
  });

  it("seeds the draft from userRules and re-seeds on prop change", () => {
    expect(src).toContain("useEffect(() => setDraft(userRules)");
  });
});
