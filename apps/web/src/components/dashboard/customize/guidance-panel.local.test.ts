import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./guidance-panel.tsx", import.meta.url),
  "utf8",
);

describe("GuidancePanel — UX restructure B soft-instructions surface", () => {
  it("renders a structured PageHint card flagging the soft boundary", () => {
    expect(src).toContain("PageHint");
    expect(src).toContain("Soft instructions");
    expect(src).toContain("not deterministically forced");
  });

  it("points users to Gates and Presets for hard enforcement", () => {
    expect(src).toContain("Gates");
    expect(src).toContain("Presets");
  });

  it("disables the save button until the draft diverges from the persisted value", () => {
    expect(src).toContain("disabled={!dirty || rulesSaving}");
  });

  it("seeds the draft from userRules and re-seeds on prop change", () => {
    expect(src).toContain("useEffect(() => setDraft(userRules)");
  });
});
