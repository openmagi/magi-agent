import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./customize-hub.tsx", import.meta.url),
  "utf8",
);

describe("CustomizeHub — Phase 4 full-page sub-nav surface", () => {
  it("declares the four sub-nav sections (no SeamSpec page until PR-C)", () => {
    expect(src).toContain('"verification"');
    expect(src).toContain('"tools"');
    expect(src).toContain('"recipes"');
    expect(src).toContain('"hooks"');
  });

  it("reuses the headless panel bodies from the legacy modals (no duplicate rendering logic)", () => {
    expect(src).toContain("VerificationRulePanel");
    expect(src).toContain("CustomToolPanel");
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
