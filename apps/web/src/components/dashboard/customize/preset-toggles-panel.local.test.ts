import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./preset-toggles-panel.tsx", import.meta.url),
  "utf8",
);

describe("PresetTogglesPanel — UX restructure B preset-only surface", () => {
  it("renders preset rows via the shared PresetRow helper (no fork)", () => {
    expect(src).toContain("PresetRow");
  });

  it("groups presets by WHEN-domain using the shared DOMAIN_LABELS map", () => {
    expect(src).toContain("DOMAIN_LABELS");
    expect(src).toContain("DOMAIN_ORDER");
  });

  it("collapses preview (not-yet-wired) presets under a details disclosure", () => {
    expect(src).toContain("Not yet wired — preview");
    expect(src).toContain("<details");
  });

  it("points users to the catalog source of truth so the surface stays auditable", () => {
    expect(src).toContain("magi_agent/customize/preset_map.py");
  });
});
