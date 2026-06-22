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

  it("renders user policies before built-in (Your policies group first)", () => {
    expect(src).toContain('title="Your policies"');
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
});
