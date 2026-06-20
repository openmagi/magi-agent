import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./verification-tabs.tsx", import.meta.url),
  "utf8",
);

describe("VerificationTabs — UX restructure B inner-tab switcher", () => {
  it("declares exactly three inner tabs: presets / gates / guidance", () => {
    expect(src).toContain('"presets"');
    expect(src).toContain('"gates"');
    expect(src).toContain('"guidance"');
    // No leftover ad-hoc fourth tab (advanced lives on its own sub-nav section).
    expect(src).not.toContain('"advanced"');
  });

  it("composes the three split panels rather than re-implementing them inline", () => {
    expect(src).toContain("PresetTogglesPanel");
    expect(src).toContain("GatesPanel");
    expect(src).toContain("GuidancePanel");
  });

  it("uses an aria-current attribute so screen readers can announce the active tab", () => {
    expect(src).toContain('aria-current={t.id === tab ? "page" : undefined}');
  });

  it("ships per-tab hint copy so users know what each tab is for", () => {
    expect(src).toContain("Soft instructions injected");
    expect(src).toContain("Toggle the built-in gates");
    expect(src).toContain("Author your own enforcement rules");
  });
});
