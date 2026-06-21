import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./customize-hub.tsx", import.meta.url),
  "utf8",
);

describe("CustomizeHub — Phase 4 full-page sub-nav surface", () => {
  it("declares the five sub-nav sections (PR-C3 added Advanced for the SeamSpec builder)", () => {
    expect(src).toContain('"verification"');
    expect(src).toContain('"tools"');
    expect(src).toContain('"recipes"');
    expect(src).toContain('"hooks"');
    expect(src).toContain('"advanced"');
  });

  it("mounts the inner-tabbed Verification surface (UX restructure B), CustomToolPanel and SeamBuilderPanel", () => {
    // VerificationTabs replaces the monolithic VerificationRulePanel mount —
    // the modal panel still ships for legacy/modal callers but the hub does
    // not import it directly anymore.
    expect(src).toContain("VerificationTabs");
    expect(src).not.toMatch(/import\s*{[^}]*VerificationRulePanel/);
    expect(src).toContain("CustomToolPanel");
    expect(src).toContain("SeamBuilderPanel");
  });

  it("renders Hooks via a PageHint card pointing to Gates/Presets for the wrong-shape cases", () => {
    expect(src).toContain("PageHint");
    expect(src).toContain("Hooks — Python callables");
  });

  it("clarifies that Advanced rewires presets rather than adding new gates", () => {
    expect(src).toContain("does NOT add a new gate");
  });

  it("ships a Phase-3-aware Recipes panel that greys out unmapped UI labels", () => {
    expect(src).toContain("RecipesPanel");
    expect(src).toContain("packIds");
    expect(src).toContain("no live effect");
  });

  it("ships a HookBus placeholder honest about the file-only authoring contract", () => {
    expect(src).toContain("HooksPanel");
    expect(src).toContain("settings.json");
    expect(src).toContain("self-host only");
  });

  it("forwards the active section through onSectionChange so the page can sync the URL", () => {
    expect(src).toContain("onSectionChange");
  });
});
