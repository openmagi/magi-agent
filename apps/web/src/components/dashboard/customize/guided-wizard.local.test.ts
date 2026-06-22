import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./guided-wizard.tsx", import.meta.url),
  "utf8",
);

describe("GuidedWizard — toss-style step-by-step policy builder (PR-E2)", () => {
  it("declares exactly five steps so the progress bar / Next button maths line up", () => {
    expect(src).toContain("STEPS");
    expect(src).toMatch(/"When\?"[\s\S]*"What evidence must pass\?"[\s\S]*"What happens if missing\?"[\s\S]*"Name your policy"[\s\S]*"Review"/);
  });

  it("renders one decision per step (Scope, Evidence, OnMissing, Name, Review)", () => {
    expect(src).toContain("ScopeStep");
    expect(src).toContain("EvidenceStep");
    expect(src).toContain("OnMissingStep");
    expect(src).toContain("NameStep");
    expect(src).toContain("ReviewStep");
  });

  it("renders an aria progressbar so screen readers can announce step N / M", () => {
    expect(src).toContain('role="progressbar"');
    expect(src).toContain("aria-valuenow={step + 1}");
    expect(src).toContain("aria-valuemax={total}");
  });

  it("badges the recommended scope + the recommended on-missing action", () => {
    expect(src).toContain('opt.recommended ? "recommended"');
    // Defaults: coding scope = recommended; block action = recommended.
    expect(src).toMatch(/"coding".*recommended: true/);
    expect(src).toMatch(/"block".*recommended: true/);
  });

  it("pulls evidence-ref options from the catalog + the user evidence types catalog", () => {
    expect(src).toContain("buildRefOptions");
    expect(src).toContain("catalog.verification.customRuleMenu");
    expect(src).toContain("evidenceTypes");
  });

  it("activates by calling putCustomRule with a deterministic_ref draft", () => {
    expect(src).toContain("putCustomRule(agentFetch, rule)");
    expect(src).toContain('kind: "deterministic_ref"');
    expect(src).toContain('payload: { ref: draft.evidenceRef }');
  });

  it("guards the Next button per step via stepIsComplete", () => {
    expect(src).toContain("stepIsComplete");
    // Step 3 (Name) requires a valid id pattern.
    expect(src).toContain("/^[a-z0-9][a-z0-9_-]{0,127}$/.test(draft.ruleId)");
  });

  it("renders a plain-English summary on the Review step (no raw JSON)", () => {
    expect(src).toContain("What this policy does");
    // Source intentionally does NOT shove raw IR JSON onto the Review step;
    // the user reads a sentence and a small key/value dl, not a JSON dump.
    expect(src).not.toContain("JSON.stringify(rule");
  });
});
