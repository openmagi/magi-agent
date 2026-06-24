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

  it("renders a prominent Advisory trust-class badge (PR-F1)", () => {
    expect(src).toContain("Trust class: Advisory");
    expect(src).toMatch(/>\s*Advisory\s*</);
  });

  it("ships honest advisory helper copy that points at deterministic surfaces", () => {
    expect(src).toContain(
      "Injected into the system prompt as operator guidance.",
    );
    expect(src).toContain("The model is");
    expect(src).toContain("asked to honor these but no gate enforces them.");
    expect(src).toContain(
      "For deterministic",
    );
    expect(src).toContain("rules use the Author wizard or NL compose.");
  });
});
