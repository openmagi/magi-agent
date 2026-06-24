import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./rules-table.tsx", import.meta.url),
  "utf8",
);

describe("RulesTable — unified Customize rules surface (Phase 1)", () => {
  it("declares all four origin kinds so every rule source lands in one list", () => {
    expect(src).toContain('"builtin"');
    expect(src).toContain('"custom"');
    expect(src).toContain('"after-tool"');
    expect(src).toContain('"seamspec"');
  });

  it("renders an origin filter chip row + per-origin count badges", () => {
    expect(src).toContain("FilterChip");
    expect(src).toContain("aria-pressed={active}");
  });

  it("groups by origin and uses an accessible expand/collapse button per group", () => {
    expect(src).toContain("OriginGroup");
    expect(src).toContain("aria-expanded={open}");
  });

  it("uses a true switch role for togglable rows", () => {
    expect(src).toContain('role="switch"');
    expect(src).toContain("aria-checked={checked}");
  });

  it("never offers a delete button for built-in rows", () => {
    // Built-in rows always set onDelete to null — the adapter never wires
    // a deletion handler since users cannot remove shipped presets.
    expect(src).toContain("onDelete: null");
  });

  it("renders one row per SeamSpec ACTION, not per doc, so the user sees the mutation", () => {
    expect(src).toContain("spec.actions.map");
  });

  it("shows a StatePill that distinguishes always-on / preview / enabled / disabled", () => {
    expect(src).toContain("StatePill");
    expect(src).toContain('"always-on"');
    expect(src).toContain('"preview"');
  });

  it("imports the shared TrustBadge and per-row helper (PR-F5)", () => {
    // The Trust column is sourced from the shared component so the
    // honesty taxonomy stays in lockstep with the GuidancePanel pill.
    expect(src).toContain("TrustBadge");
    expect(src).toContain("trustClassForPolicy");
    expect(src).toMatch(/from\s+"\.\/trust-badge"/);
  });

  it("renders <TrustBadge> inside the per-row view so every rule shows its trust class (PR-F5)", () => {
    // The pill must appear in the row body, not just the imports.
    expect(src).toContain("<TrustBadge");
    expect(src).toContain('ariaLabel="Trust class for this policy"');
  });
});
