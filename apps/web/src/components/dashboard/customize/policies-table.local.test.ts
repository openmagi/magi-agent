import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./policies-table.tsx", import.meta.url),
  "utf8",
);

describe("PoliciesTable — unified policy surface (PR-E1)", () => {
  it("imports the unified Policy type from policy-model", () => {
    expect(src).toContain('from "@/lib/policy-model"');
    expect(src).toContain("type { Policy");
  });

  it("ships filter chips for Custom and Built-in origins", () => {
    expect(src).toContain('label="Custom"');
    expect(src).toContain('label="Built-in"');
    expect(src).toContain("aria-pressed={active}");
  });

  it("renders user rules before built-in (Your rules group first)", () => {
    // PR-P1: the Rules tab uses "rules" terminology, not "policies".
    expect(src).toContain('title="Your rules"');
    expect(src).toContain("userPolicies.length > 0");
  });

  it("dispatches toggle/delete handlers by rawSource.kind", () => {
    // Every backend kind has an explicit branch — no silent fall-through.
    expect(src).toContain('case "preset_seam":');
    expect(src).toContain('case "custom_rule":');
    expect(src).toContain('case "dashboard_check":');
    expect(src).toContain('case "seam_spec":');
  });

  it("uses a true switch role for togglable rows (accessibility)", () => {
    expect(src).toContain('role="switch"');
    expect(src).toContain("aria-checked={checked}");
  });

  it("hides delete button for built-in / non-deletable rows", () => {
    expect(src).toContain("policy.deletable ? (");
  });

  it("ships scope + firesAt + search filters in addition to origin (PR-E4)", () => {
    expect(src).toContain("scopeFilter");
    expect(src).toContain("firesAtFilter");
    expect(src).toContain('type="search"');
    expect(src).toContain('aria-label="Search rules"');
  });

  it("matches the search needle against name + description + condition summary", () => {
    expect(src).toContain("p.name");
    expect(src).toContain("p.description");
    expect(src).toContain("p.condition.summary");
  });

  it("PR-P2: pulls not-wired (preview) rules into a collapsed Dormant group", () => {
    // Dormant rules are separated out of the live user/built-in groups and
    // shown collapsed so the main list only has rules that actually gate.
    expect(src).toContain('p.state === "preview"');
    expect(src).toContain("dormantPolicies");
    expect(src).toContain("Dormant");
    expect(src).toContain("not wired yet");
    // The badge pill reads "not wired", not "preview".
    expect(src).toContain('preview: "not wired"');
    // Dormant group starts collapsed.
    expect(src).toMatch(/title=\{`Dormant[\s\S]*?defaultOpen=\{false\}/);
  });

  it("PR-U4a: renders a 'scoped in N modes' reverse-cross-link badge", () => {
    // The badge only appears when the policy id is present in scopedInModes.
    expect(src).toContain("scopedInModes");
    expect(src).toContain("scopedModes");
    expect(src).toContain("scoped in ");
    expect(src).toContain("scopedInModes?.[p.id]");
    // Threaded through the Group so both origin sections show it.
    expect(src).toContain("scopedInModes={scopedInModes}");
  });
});
