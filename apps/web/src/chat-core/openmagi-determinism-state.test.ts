import { describe, expect, it } from "vitest";
import { applyDeterministicRuntimeEvent } from "./openmagi-determinism-state";
import type { DeterministicRuntimeState } from "./types";

describe("deterministic runtime state reducer", () => {
  it("records workflow and policy snapshot", () => {
    const state = applyDeterministicRuntimeEvent(undefined, {
      type: "deterministic_workflow",
      workflowId: "workflow.public",
      workflowVersion: "1.0.0",
      routeId: "route-public",
      governed: true,
      effectivePolicySnapshotDigest: `sha256:${"1".repeat(64)}`,
      ledgerHeadDigest: `sha256:${"2".repeat(64)}`,
      checkpointId: "checkpoint-1",
    });

    expect(state.workflowId).toBe("workflow.public");
    expect(state.governed).toBe(true);
    expect(state.effectivePolicySnapshotDigest).toMatch(/^sha256:/);
  });

  it("keeps only recent guardrails", () => {
    let state: DeterministicRuntimeState | undefined;
    for (let i = 0; i < 16; i += 1) {
      state = applyDeterministicRuntimeEvent(state, {
        type: "deterministic_guardrail",
        guardrailId: `g${i}`,
        stage: "before_output_projection",
        status: "passed",
        reasonCodes: [],
        policyDecisionId: `p${i}`,
        validatorTrustClass: "deterministic",
        evidenceRefs: [],
        redactionStatus: "redacted",
      });
    }

    expect(state?.guardrails).toHaveLength(12);
    expect(state?.guardrails?.[0]?.guardrailId).toBe("g4");
  });

  it("records projection and fallback summaries", () => {
    const projected = applyDeterministicRuntimeEvent(undefined, {
      type: "deterministic_projection",
      projectionMode: "structured_claims_only",
      outputAllowed: false,
      blockedReasonCodes: ["missing_evidence"],
      claimCount: 3,
      renderedClaimCount: 1,
    });
    const state = applyDeterministicRuntimeEvent(projected, {
      type: "deterministic_fallback",
      fromAuthority: "python",
      toAuthority: "typescript",
      reasonCode: "projection_blocked",
      requestDigest: `sha256:${"3".repeat(64)}`,
    });

    expect(state?.projectionMode).toBe("structured_claims_only");
    expect(state?.blockedReasonCodes).toEqual(["missing_evidence"]);
    expect(state?.fallbackReasonCode).toBe("projection_blocked");
    expect(state?.fallbackAuthority).toBe("typescript");
  });

  it("records applied recipes and verification gates", () => {
    const withRecipe = applyDeterministicRuntimeEvent(undefined, {
      type: "deterministic_recipe_selection",
      selectionSource: "explicit",
      status: "explicit_applied",
      requestedRecipeRefs: [{
        recipeId: "invoice.cited-brief",
        version: "1.0.0",
      }],
      appliedRecipeRefs: [{
        recipeId: "invoice.cited-brief",
        version: "1.0.0",
        digest: `sha256:${"1".repeat(64)}`,
      }],
      omittedRecipeRefs: [],
      omissionReasons: [],
      policySnapshotDigest: `sha256:${"1".repeat(64)}`,
      appliedRecipes: [{
        recipeId: "invoice.cited-brief",
        version: "1.0.0",
        role: "primary",
        governed: true,
        sourceDigest: `sha256:${"1".repeat(64)}`,
      }],
    });
    const state = applyDeterministicRuntimeEvent(withRecipe, {
      type: "deterministic_verification_gate",
      gateId: "citation.opened_snapshot",
      stage: "before_output_projection",
      status: "passed",
      validatorTrustClass: "deterministic",
      reasonCodes: [],
      evidenceRefs: [`evidence:sha256:${"2".repeat(64)}`],
      policyDecisionId: "policy-1",
      checkedAt: 1760000000000,
    });

    expect(state?.appliedRecipes).toEqual([
      expect.objectContaining({
        recipeId: "invoice.cited-brief",
        role: "primary",
        governed: true,
      }),
    ]);
    expect(state?.recipeSelection).toMatchObject({
      status: "explicit_applied",
      selectionSource: "explicit",
      policySnapshotDigest: `sha256:${"1".repeat(64)}`,
    });
    expect(state?.verificationGates).toEqual([
      expect.objectContaining({
        gateId: "citation.opened_snapshot",
        status: "passed",
        policyDecisionId: "policy-1",
      }),
    ]);
  });

  it("records blocked explicit recipe selection without marking it as success", () => {
    const state = applyDeterministicRuntimeEvent(undefined, {
      type: "deterministic_recipe_selection",
      selectionSource: "explicit",
      status: "explicit_blocked",
      requestedRecipeRefs: [{
        recipeId: "openmagi.research",
        version: "1",
      }],
      omittedRecipeRefs: [{
        recipeId: "openmagi.research",
        version: "1",
      }],
      omissionReasons: ["recipe_policy_blocked"],
      policySnapshotDigest: `sha256:${"5".repeat(64)}`,
      turnBlocked: true,
      fallbackUsed: false,
      nextAction: "choose_available_recipe",
      appliedRecipes: [],
    });

    expect(state?.recipeSelection).toEqual(expect.objectContaining({
      status: "explicit_blocked",
      turnBlocked: true,
      fallbackUsed: false,
      omissionReasons: ["recipe_policy_blocked"],
      nextAction: "choose_available_recipe",
    }));
    expect(state?.appliedRecipes).toEqual([]);
  });

  it("does not create deterministic state from text-only public events", () => {
    const existing: DeterministicRuntimeState = { workflowId: "workflow.public" };

    expect(applyDeterministicRuntimeEvent(undefined, { type: "text_delta", delta: "ok" })).toBeUndefined();
    expect(applyDeterministicRuntimeEvent(existing, { type: "thinking_delta", delta: "hidden" })).toBe(existing);
  });
});
