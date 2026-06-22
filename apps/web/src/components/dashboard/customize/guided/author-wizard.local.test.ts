import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./author-wizard.tsx", import.meta.url),
  "utf8",
);

describe("AuthorWizard — unified 6-step policy authoring (PR-E5)", () => {
  it("declares exactly six steps", () => {
    expect(src).toContain("const TOTAL = 6");
  });

  it("ships one step per axis: trigger / archetype / condition / specifics / name / review", () => {
    expect(src).toContain("TriggerStep");
    expect(src).toContain("ArchetypeStep");
    expect(src).toContain("ConditionKindStep");
    expect(src).toContain("SpecificsStep");
    expect(src).toContain("NameStep");
    expect(src).toContain("ReviewStep");
  });

  it("step 1 (TriggerStep) renders TWO radio fieldsets (lifecycle + scope)", () => {
    // Each axis lives in its own fieldset for screen-reader scoping —
    // mirrors control-plane's compound step layout from Image 12-17.
    expect(src.match(/<fieldset/g)?.length).toBe(2);
    expect(src).toContain("Lifecycle event");
    expect(src).toContain("Turn scope");
  });

  it("archetype options are FILTERED by lifecycle (no static list)", () => {
    expect(src).toContain("availableArchetypes");
    expect(src).toMatch(/before_tool_use[\s\S]*"block", "ask", "audit"/);
    expect(src).toMatch(/after_tool_use[\s\S]*"block", "audit", "strip"/);
  });

  it("condition kind options are FILTERED by (lifecycle, archetype)", () => {
    expect(src).toContain("availableConditionKinds");
    expect(src).toContain("before_tool_use");
    expect(src).toContain("after_tool_use");
    expect(src).toContain("pre_final");
  });

  it("downstream fields auto-reseed when an upstream axis changes", () => {
    expect(src).toContain("reseedDownstream");
  });

  it("surfaces 'emit signal' archetype as disabled (Coming soon) per Kevin's call", () => {
    expect(src).toContain("Coming soon");
    expect(src).toContain('id: "emit"');
    expect(src).toContain("disabled: true");
  });

  it("routes (after_tool_use, regex, audit/block) to putDashboardCheck", () => {
    expect(src).toContain("putDashboardCheck");
    expect(src).toMatch(
      /draft\.lifecycle === "after_tool_use"\s*&&\s*draft\.conditionKind === "regex"/,
    );
  });

  it("routes all other shapes to putCustomRule with the right kind", () => {
    expect(src).toContain("putCustomRule");
    expect(src).toContain('return "tool_perm"');
    expect(src).toContain('if (draft.conditionKind === "evidence_ref") return "deterministic_ref"');
    expect(src).toContain('if (draft.conditionKind === "shacl") return "shacl_constraint"');
  });

  it("Review step emits a plain-English sentence + key/value summary (no raw JSON)", () => {
    expect(src).toContain("describePolicy");
    expect(src).not.toContain("JSON.stringify");
  });

  it("Save button (last step) calls handleSave", () => {
    expect(src).toContain("handleSave");
    expect(src).toContain("onSave={handleSave}");
  });
});
