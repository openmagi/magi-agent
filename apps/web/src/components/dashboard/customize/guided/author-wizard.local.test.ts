import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./author-wizard.tsx", import.meta.url),
  "utf8",
);

describe("AuthorWizard — variable-length policy authoring (F1.5)", () => {
  it("declares step plan as a function of lifecycle (pre_final=6, tool-bearing=7)", () => {
    // F1.5 separates tool targeting from per-call condition. pre_final has
    // no tool layer so it stays 6 steps; tool-bearing lifecycles gain a
    // dedicated "Target" step (Any tool / Specific tool) for a total of 7.
    expect(src).toContain("function stepPlan(lifecycle: Lifecycle): StepKey[]");
    expect(src).toMatch(
      /pre_final[\s\S]*?\["trigger", "condition", "specifics", "action", "name", "review"\]/,
    );
    expect(src).toContain(
      '["trigger", "target", "condition", "specifics", "action", "name", "review"]',
    );
  });

  it("ships one step body per axis: trigger / target / condition / specifics / archetype / name / review", () => {
    expect(src).toContain("TriggerStep");
    expect(src).toContain("TargetStep");
    expect(src).toContain("ConditionKindStep");
    expect(src).toContain("SpecificsStep");
    expect(src).toContain("ArchetypeStep");
    expect(src).toContain("NameStep");
    expect(src).toContain("ReviewStep");
  });

  it("TargetStep is keyed off the dynamic step plan (currentKey === 'target')", () => {
    // Target step is rendered conditionally on the StepKey enum, not a
    // hardcoded index, so pre_final can skip it.
    expect(src).toMatch(/currentKey === "target"[\s\S]*?TargetStep/);
  });

  it("Target step surfaces Any tool / Specific tool radio + tool-name input", () => {
    expect(src).toContain("Which tool(s) does this policy apply to?");
    expect(src).toContain("Any tool");
    expect(src).toContain("Specific tool");
    expect(src).toContain('toolTarget === "specific"');
  });

  it("TriggerStep renders TWO radio fieldsets (lifecycle + scope)", () => {
    expect(src.match(/<fieldset/g)?.length).toBe(2);
    expect(src).toContain("Lifecycle event");
    expect(src).toContain("Turn scope");
  });

  it("drops the disabled 'emit' archetype (audit+(no condition) covers the same outcome)", () => {
    expect(src).not.toContain("Coming soon");
    expect(src).not.toContain("Megaphone");
    expect(src).not.toMatch(/id:\s*"emit"/);
    expect(src).toContain('type Archetype = "block" | "ask" | "audit" | "strip"');
  });

  it("ConditionKind drops 'tool_name' (now promoted to TargetStep)", () => {
    // Conflation of "which tool" with "what condition" is the F1.5 fix.
    // Tool selection moves to TargetStep; the condition list shrinks.
    expect(src).not.toMatch(/type ConditionKind[\s\S]*?"tool_name"/);
  });

  it("availableConditionKinds is FILTERED by lifecycle AND tool target", () => {
    expect(src).toContain(
      "availableConditionKinds(\n  lifecycle: Lifecycle,\n  toolTarget: ToolTarget,\n)",
    );
  });

  it("before_tool_use + target=specific exposes ONLY 'none' (per-tool unconditional)", () => {
    // Backend tool_perm has no AND between tool name and url-shape matchers,
    // so per-tool rules can only fire unconditionally per call. The wizard
    // shrinks the option list to match.
    expect(src).toMatch(
      /toolTarget === "specific"[\s\S]*?return \["none"\]/,
    );
  });

  it("before_tool_use + target=any omits 'none' (no wildcard matcher in backend)", () => {
    // tool_perm has no wildcard, so 'no condition' with target=any has no
    // honest backend mapping. The option is omitted instead of synthesised.
    expect(src).toMatch(
      /target=any: tool_perm has no wildcard[\s\S]*?return \["domain", "domain_allowlist"\]/,
    );
  });

  it("after_tool_use + target=specific omits 'llm_criterion' (no per-tool filter today)", () => {
    expect(src).toMatch(
      /llm_criterion has no per-tool filter[\s\S]*?return \["none", "regex"\]/,
    );
  });

  it("after_tool_use + target=any offers none / regex / llm_criterion", () => {
    expect(src).toMatch(/return \["none", "regex", "llm_criterion"\]/);
  });

  it("pre_final ignores target and returns evidence_ref / shacl / llm_criterion", () => {
    expect(src).toMatch(
      /pre_final[\s\S]*?return \["evidence_ref", "shacl", "llm_criterion"\]/,
    );
  });

  it("action archetypes are FILTERED by lifecycle only", () => {
    expect(src).toContain("availableArchetypes");
    expect(src).toMatch(/before_tool_use[\s\S]*?"block", "ask", "audit"/);
    expect(src).toMatch(/after_tool_use[\s\S]*?"block", "audit", "strip"/);
  });

  it("action step header composes a per-trigger phrase (target + condition together)", () => {
    expect(src).toContain("triggerEventPhrase");
    expect(src).toContain("targetEventPhrase");
    expect(src).toContain("did NOT return ok");
    expect(src).toContain("does NOT conform");
  });

  it("specifics step auto-skips when conditionKind === 'none'", () => {
    expect(src).toContain("isSpecificsEmpty");
    expect(src).toMatch(/draft\.conditionKind === "none"/);
  });

  it("downstream fields auto-reseed when an upstream axis changes", () => {
    expect(src).toContain("reseedDownstream");
    // reseed must consider both lifecycle AND target when filtering kinds.
    expect(src).toContain(
      "availableConditionKinds(merged.lifecycle, merged.toolTarget)",
    );
  });

  it("after-tool DashboardCheck path honors target=specific by setting tool=<name>", () => {
    expect(src).toContain("putDashboardCheck");
    expect(src).toMatch(
      /toolTarget === "specific" \? draft\.toolName\.trim\(\) : "\*"/,
    );
  });

  it("after-tool 'no condition' synthesises pattern='.*' for the DashboardCheck", () => {
    expect(src).toMatch(/conditionKind === "none" \? "\.\*"/);
  });

  it("before-tool tool_perm payload picks match from target + condition", () => {
    expect(src).toContain("customRulePayload");
    expect(src).toContain('toolTarget === "specific"');
    expect(src).toContain("match: { tool: draft.toolName.trim() }");
    expect(src).toContain("match: { domain: draft.domain.trim() }");
    expect(src).toContain("domainAllowlist:");
  });

  it("Review step shows Target row only for tool-bearing lifecycles", () => {
    expect(src).toMatch(/draft\.lifecycle !== "pre_final"[\s\S]*?Target/);
  });

  it("Review step emits plain-English sentence + key/value summary (no raw JSON)", () => {
    expect(src).toContain("describePolicy");
    expect(src).not.toContain("JSON.stringify");
  });

  it("Save button (last step) calls handleSave", () => {
    expect(src).toContain("handleSave");
    expect(src).toContain("onSave={handleSave}");
  });
});
