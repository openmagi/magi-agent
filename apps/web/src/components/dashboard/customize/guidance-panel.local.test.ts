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

  it("renders a prominent Advisory trust-class badge (PR-F1, via shared TrustBadge after F5)", () => {
    // Post-F5 the visible Advisory pill is rendered by the shared TrustBadge
    // (advisory variant), which encapsulates the "Trust class: Advisory"
    // aria-label and the visible "Advisory" text. Verifying the component
    // usage here preserves the F1 contract without re-asserting the inlined
    // markup that the F5 refactor intentionally removed.
    expect(src).toContain('<TrustBadge trustClass="advisory"');
  });

  it("reuses the shared TrustBadge component instead of the inline pill (PR-F5)", () => {
    expect(src).toContain("TrustBadge");
    expect(src).toMatch(/from\s+["'][^"']*trust-badge["']/);
    expect(src).toContain('<TrustBadge trustClass="advisory"');
  });

  it("removes the legacy inline Advisory <span> pill markup (PR-F5)", () => {
    // The old hand-rolled span used bg-amber-500/10 + uppercase tracking-wide.
    // It must be replaced by the shared component (still amber-styled) so a
    // single primitive owns the trust-class visual contract.
    expect(src).not.toMatch(/<span[^>]*aria-label="Trust class: Advisory"/);
    expect(src).not.toContain("bg-amber-500/10 px-2 py-0.5 text-[10px]");
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
