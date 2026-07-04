import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(new URL("./customize-api.ts", import.meta.url), "utf8");

describe("customize-api: conversational policy compile + persist", () => {
  it("exposes compilePolicyInteractive targeting the interactive endpoint", () => {
    expect(src).toContain("export async function compilePolicyInteractive");
    expect(src).toContain("/v1/app/policies/compile/interactive");
  });

  it("exposes savePolicyFromPlan targeting the from-plan endpoint and sending {plan}", () => {
    expect(src).toContain("export async function savePolicyFromPlan");
    expect(src).toContain("/v1/app/policies/from-plan");
    expect(src).toMatch(/savePolicyFromPlan[\s\S]{0,600}JSON\.stringify\(\{\s*plan\s*\}\)/);
  });

  it("declares the policy interactive request/response contract", () => {
    expect(src).toContain("export interface PolicyInteractiveRequest");
    expect(src).toContain("export interface PolicyInteractiveResponse");
    // Multi-turn shape: params + plan + ready_to_save + questions + missing_params.
    for (const field of ["params", "plan", "ready_to_save", "questions", "missing_params"]) {
      expect(src).toContain(field);
    }
    expect(src).toContain("paramsSoFar");
  });

  it("declares the from-plan response with the saved ids", () => {
    expect(src).toContain("export interface PolicyFromPlanResponse");
    for (const field of ["policyId", "producerId", "gateId"]) {
      expect(src).toContain(field);
    }
  });

  it("is non-throwing: both helpers return an {ok:false, error} envelope on failure", () => {
    // Each helper wraps in try/catch and returns ok:false rather than throwing
    // (mirrors compileCustomRuleInteractive's thin-shell contract).
    const interactive = src.slice(src.indexOf("export async function compilePolicyInteractive"));
    expect(interactive).toMatch(/try\s*\{[\s\S]{0,900}catch/);
    expect(interactive).toContain("ok: false");
    const save = src.slice(src.indexOf("export async function savePolicyFromPlan"));
    expect(save).toMatch(/try\s*\{[\s\S]{0,900}catch/);
    expect(save).toContain("ok: false");
  });

  it("reuses the shared InteractiveHistoryTurn / InteractiveQuestion types", () => {
    // The policy request carries history as InteractiveHistoryTurn[] and the
    // response's questions are InteractiveQuestion[] (no duplicate shapes).
    expect(src).toMatch(/PolicyInteractiveRequest[\s\S]{0,200}InteractiveHistoryTurn\[\]/);
    expect(src).toMatch(/PolicyInteractiveResponse[\s\S]{0,400}InteractiveQuestion\[\]/);
  });

  it("exposes reviewPolicyPlan targeting the review endpoint (advisory)", () => {
    expect(src).toContain("export async function reviewPolicyPlan");
    expect(src).toContain("/v1/app/policies/review");
    expect(src).toContain("export interface PolicyReviewResponse");
    // The verdict shape carries the four intent-coverage verdicts + the
    // deterministic structural findings.
    expect(src).toContain("export interface PolicyReviewVerdict");
    for (const v of ["aligned", "partial", "misaligned", "unknown"]) {
      expect(src).toContain(`"${v}"`);
    }
    expect(src).toContain("structural");
    expect(src).toContain("structurallySound");
    // Non-throwing envelope, like the other policy helpers.
    const review = src.slice(src.indexOf("export async function reviewPolicyPlan"));
    expect(review).toMatch(/try\s*\{[\s\S]{0,900}catch/);
    expect(review).toContain("ok: false");
  });

  it("declares producer_reused on the interactive response", () => {
    // The field lands inside the PolicyInteractiveResponse interface (which
    // carries long JSDoc), so assert presence + that the interface exists
    // rather than a brittle proximity window.
    expect(src).toContain("export interface PolicyInteractiveResponse");
    expect(src).toContain("producer_reused");
  });
});
