import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./author-wizard.tsx", import.meta.url),
  "utf8",
);

describe("AuthorWizard — unified 6-step policy authoring", () => {
  it("declares exactly six steps", () => {
    expect(src).toContain("const TOTAL = 6");
  });

  it("ships one step per axis: trigger / condition / specifics / archetype / name / review", () => {
    expect(src).toContain("TriggerStep");
    expect(src).toContain("ConditionKindStep");
    expect(src).toContain("SpecificsStep");
    expect(src).toContain("ArchetypeStep");
    expect(src).toContain("NameStep");
    expect(src).toContain("ReviewStep");
  });

  it("renders steps in condition-before-action order (0→1→2→3→4→5)", () => {
    // step 1 must render ConditionKindStep, step 3 must render ArchetypeStep —
    // this guarantees the user picks WHAT to check before HOW to react,
    // matching the mental model that the action step's phrasing can
    // reflect the chosen trigger.
    expect(src).toMatch(/step === 1[\s\S]*?ConditionKindStep/);
    expect(src).toMatch(/step === 3[\s\S]*?ArchetypeStep/);
  });

  it("step 1 (TriggerStep) renders TWO radio fieldsets (lifecycle + scope)", () => {
    // Each axis lives in its own fieldset for screen-reader scoping.
    expect(src.match(/<fieldset/g)?.length).toBe(2);
    expect(src).toContain("Lifecycle event");
    expect(src).toContain("Turn scope");
  });

  it("drops the disabled 'emit' archetype now that audit+(no condition) covers the same outcome", () => {
    expect(src).not.toContain("Coming soon");
    expect(src).not.toContain("Megaphone");
    expect(src).not.toMatch(/id:\s*"emit"/);
    // Archetype union shrinks to four members.
    expect(src).toContain('type Archetype = "block" | "ask" | "audit" | "strip"');
  });

  it("condition kinds are FILTERED by lifecycle (no archetype dependency)", () => {
    // Function signature changed: only lifecycle is needed because action
    // step now comes after condition.
    expect(src).toContain("availableConditionKinds(lifecycle: Lifecycle)");
  });

  it("exposes '(no condition)' first-class ONLY for after_tool_use (backend support honest)", () => {
    // before_tool_use tool_perm has no wildcard matcher; pre_final rules
    // have no always-fail sentinel — listing 'none' there would require a
    // fake-condition workaround.
    expect(src).toMatch(/after_tool_use[\s\S]*?"none", "regex", "llm_criterion"/);
    expect(src).toMatch(/before_tool_use[\s\S]*?"tool_name", "domain", "domain_allowlist"/);
    // pre_final list MUST NOT contain "none".
    const preFinalList = src.match(/return \[(?:"evidence_ref"|"shacl"|"llm_criterion"|, |\s)+\]/);
    expect(preFinalList?.[0]).not.toContain('"none"');
  });

  it("labels fetch-only condition kinds with '(network tools only)' so users see the constraint", () => {
    // tool_perm's domain/domain_allowlist matchers only fire for tools
    // that surface a URL argument — surface that honestly.
    expect(src).toMatch(/Fetch domain \(network tools only\)/);
    expect(src).toMatch(/Domain allowlist \(network tools only\)/);
  });

  it("action archetypes are FILTERED by lifecycle only (no static list)", () => {
    expect(src).toContain("availableArchetypes");
    expect(src).toMatch(/before_tool_use[\s\S]*?"block", "ask", "audit"/);
    expect(src).toMatch(/after_tool_use[\s\S]*?"block", "audit", "strip"/);
  });

  it("action step header reflects the chosen condition trigger (positive/negative semantics preserved)", () => {
    // PR-E5 unified into a generic "when the condition fires" phrasing
    // which lost the distinction between before_tool (match) and
    // pre_final (fail). The new step composes a per-condition phrase.
    expect(src).toContain("triggerEventPhrase");
    expect(src).toContain("On every trigger");
    expect(src).toContain("did NOT return ok");
    expect(src).toContain("does NOT conform");
  });

  it("specifics step auto-skips when conditionKind === 'none'", () => {
    expect(src).toContain("skipsSpecificsStep");
    expect(src).toMatch(/draft\.conditionKind === "none"/);
  });

  it("downstream fields auto-reseed when an upstream axis changes", () => {
    expect(src).toContain("reseedDownstream");
  });

  it("routes (after_tool_use, none, audit/block) to putDashboardCheck with pattern='.*'", () => {
    expect(src).toContain("putDashboardCheck");
    expect(src).toMatch(
      /draft\.conditionKind === "none"[\s\S]*?pattern: "\.\*"/,
    );
  });

  it("routes (after_tool_use, regex, audit/block) to putDashboardCheck", () => {
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
