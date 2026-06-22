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
});
