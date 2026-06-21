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

  it("renders each domain in a collapsible group with an enabled-count badge", () => {
    // Custom CollapsibleGroup component (not native <details>) so the badge
    // can sit beside the chevron and we can drive open-state from React.
    expect(src).toContain("CollapsibleGroup");
    expect(src).toContain("aria-expanded={open}");
    // enabled / total badge derived from presetOverrides ?? defaultEnabled.
    expect(src).toContain('badge={`${enabled}/${list.length}`}');
  });

  it("ships an Expand-all / Collapse-all bar", () => {
    expect(src).toContain("Expand all");
    expect(src).toContain("Collapse all");
  });

  it("defaults the preview (not-yet-wired) group collapsed", () => {
    expect(src).toContain("Not yet wired — preview");
    expect(src).toContain("PREVIEW_GROUP_KEY");
    expect(src).toContain("new Set([PREVIEW_GROUP_KEY])");
  });

  it("points users to the catalog source of truth so the surface stays auditable", () => {
    expect(src).toContain("magi_agent/customize/preset_map.py");
  });
});
