import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./nl-rule-compose.tsx", import.meta.url),
  "utf8",
);

describe("NlRuleCompose — Unified NL → rule compose surface (PR-D2)", () => {
  it("calls the unified compileRule API client (not the SHACL or SeamSpec specific one)", () => {
    expect(src).toContain("compileRule");
    expect(src).not.toContain("compileCustomRule(");
    expect(src).not.toContain("compileSeamSpec(");
  });

  it("routes Activate by routedKind to all three persistence endpoints", () => {
    expect(src).toContain("putCustomRule");
    expect(src).toContain("putSeamSpec");
    expect(src).toContain("putDashboardCheck");
  });

  it("names all six routed kinds in the ROUTED_LABEL map so no kind renders as a blank string", () => {
    expect(src).toContain("deterministic_ref:");
    expect(src).toContain("tool_perm:");
    expect(src).toContain("llm_criterion:");
    expect(src).toContain("shacl_constraint:");
    expect(src).toContain("seam_spec:");
    expect(src).toContain("custom_check:");
  });

  it("disables Activate when schemaIssues are present (deterministic gate is the last line of defence)", () => {
    expect(src).toContain("schemaIssues?.length ?? 0) === 0");
    expect(src).toContain("disabled={!canActivate || activateBusy}");
  });

  it("renders all three signals — routedKind label, reviewer verdict, schema-issues", () => {
    expect(src).toContain("Routed to");
    expect(src).toContain("Reviewer verdict");
    expect(src).toContain("Schema check (deterministic)");
  });

  it("surfaces clarifying questions on the ambiguous branch", () => {
    expect(src).toContain("Compiler needs clarification");
    expect(src).toContain("clarifyingQuestions");
  });

  it("renders the plain-English explanation returned by the compiler", () => {
    expect(src).toContain("This rule will:");
    expect(src).toContain("result.explanation");
  });

  it("collapses the raw draft JSON behind a details disclosure", () => {
    expect(src).toContain("View raw draft JSON");
  });

  it("mounts the NlRuleGuide so users see WHEN/WHAT/CONDITION axes + examples", () => {
    expect(src).toContain("NlRuleGuide");
    expect(src).toContain("onPickExample={(text) => setNlText(text)}");
  });
});

describe("NlRuleCompose — F3 field_constraint chip renderer + honest-degrade", () => {
  it("labels the new field_constraint routedKind so it does not render as a blank string", () => {
    expect(src).toContain("field_constraint:");
  });

  it("renders the structured field_constraint draft as editable chips (evidence type | field | operator | value)", () => {
    expect(src).toContain("FieldConstraintChips");
    expect(src).toContain('routedKind === "field_constraint"');
    expect(src).toContain("Evidence type");
    expect(src).toContain("Field");
    expect(src).toContain("Operator");
    expect(src).toContain("Value");
  });

  it("renders a cross-record sub-display when operator is forEachExistsCovering", () => {
    expect(src).toContain("forEachExistsCovering");
    expect(src).toContain("Source");
    expect(src).toContain("Target");
  });

  it("renders the honest-degrade red banner when error === 'field_not_in_catalog'", () => {
    expect(src).toContain('"field_not_in_catalog"');
    expect(src).toContain(
      "This rule references a field that isn't emitted as structured evidence yet",
    );
    expect(src).toContain("missingFields");
  });

  it("offers a 'Browse available fields' button and an 'Author as advisory llm_criterion instead?' link", () => {
    expect(src).toContain("Browse available fields");
    expect(src).toContain("Author as advisory llm_criterion instead?");
  });

  it("re-compiles with an advisory llm_criterion hint when the secondary action fires", () => {
    expect(src).toContain("handleDegradeToAdvisory");
    expect(src).toContain("Advisory");
  });
});
