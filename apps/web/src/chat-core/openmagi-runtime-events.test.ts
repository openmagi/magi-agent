import { describe, expect, it } from "vitest";
import {
  isPublicRuntimeEventPayload,
  parseOpenMagiRuntimeEvent,
} from "./openmagi-runtime-events";
import {
  pythonAdkDefaultOffSurfaceEvents,
  pythonAdkPrivatePayloadEvents,
  pythonAdkPublicReplayEvents,
} from "./fixtures/python-adk-public-events";

describe("OpenMagi public runtime event parser", () => {
  it("accepts a sanitized text delta", () => {
    expect(parseOpenMagiRuntimeEvent({ type: "text_delta", delta: "hello" })).toEqual({
      type: "text_delta",
      delta: "hello",
    });
  });

  it("accepts a sanitized thinking delta", () => {
    expect(parseOpenMagiRuntimeEvent({ type: "thinking_delta", delta: "checking" })).toEqual({
      type: "thinking_delta",
      delta: "checking",
    });
  });

  it("accepts deterministic guardrail events with digest-only refs", () => {
    const event = parseOpenMagiRuntimeEvent({
      type: "deterministic_guardrail",
      guardrailId: "claim-citation-gate",
      stage: "before_output_projection",
      status: "blocked",
      reasonCodes: ["unsupported_claim"],
      policyDecisionId: "policy_decision_01",
      validatorTrustClass: "deterministic",
      evidenceRefs: ["evidence:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
      redactionStatus: "redacted",
    });

    expect(event).toMatchObject({
      type: "deterministic_guardrail",
      status: "blocked",
      validatorTrustClass: "deterministic",
    });
  });

  it("accepts deterministic workflow, projection, and fallback events", () => {
    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_workflow",
      workflowId: "workflow.public",
      workflowVersion: "1.0.0",
      effectivePolicySnapshotDigest: `sha256:${"1".repeat(64)}`,
      ledgerHeadDigest: `sha256:${"2".repeat(64)}`,
      checkpointId: "checkpoint-1",
      routeId: "route-public",
      governed: true,
    })).toMatchObject({
      type: "deterministic_workflow",
      governed: true,
      effectivePolicySnapshotDigest: `sha256:${"1".repeat(64)}`,
    });

    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_projection",
      projectionMode: "structured_claims_only",
      outputAllowed: false,
      blockedReasonCodes: ["unsupported_claim"],
      claimCount: 2,
      renderedClaimCount: 1,
    })).toMatchObject({
      type: "deterministic_projection",
      outputAllowed: false,
      blockedReasonCodes: ["unsupported_claim"],
    });

    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_fallback",
      fromAuthority: "python",
      toAuthority: "typescript",
      reasonCode: "python_unavailable",
      requestDigest: `sha256:${"3".repeat(64)}`,
    })).toMatchObject({
      type: "deterministic_fallback",
      toAuthority: "typescript",
      reasonCode: "python_unavailable",
    });
  });

  it("accepts applied recipe summaries", () => {
    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_recipe_selection",
      selectionSource: "explicit",
      status: "explicit_applied",
      requestedRecipeRefs: [{
        recipeId: "openmagi.research",
        version: "1",
        digest: `sha256:${"6".repeat(64)}`,
      }],
      appliedRecipeRefs: [{
        recipeId: "openmagi.research",
        version: "1",
        digest: `sha256:${"6".repeat(64)}`,
      }],
      policySnapshotDigest: `sha256:${"5".repeat(64)}`,
      appliedRecipes: [{
        recipeId: "invoice.cited-brief",
        version: "1.0.0",
        role: "primary",
        governed: true,
        sourceDigest: `sha256:${"1".repeat(64)}`,
      }],
    })).toMatchObject({
      type: "deterministic_recipe_selection",
      status: "explicit_applied",
      selectionSource: "explicit",
      policySnapshotDigest: `sha256:${"5".repeat(64)}`,
      requestedRecipeRefs: [expect.objectContaining({
        recipeId: "openmagi.research",
      })],
      appliedRecipes: [expect.objectContaining({
        recipeId: "invoice.cited-brief",
        role: "primary",
      })],
    });
  });

  it("accepts blocked explicit recipe selection without raw policy data", () => {
    expect(parseOpenMagiRuntimeEvent({
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
      rawPolicyDump: "remove me",
      hiddenConfig: "remove me",
    })).toMatchObject({
      type: "deterministic_recipe_selection",
      status: "explicit_blocked",
      turnBlocked: true,
      fallbackUsed: false,
      omittedRecipeRefs: [expect.objectContaining({
        recipeId: "openmagi.research",
      })],
      omissionReasons: ["recipe_policy_blocked"],
    });
  });

  it("accepts verification gate summaries", () => {
    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_verification_gate",
      gateId: "citation.opened_snapshot",
      stage: "before_output_projection",
      status: "passed",
      validatorTrustClass: "deterministic",
      reasonCodes: [],
      evidenceRefs: [`evidence:sha256:${"2".repeat(64)}`],
      policyDecisionId: "policy-1",
      checkedAt: 1760000000000,
    })).toMatchObject({
      type: "deterministic_verification_gate",
      gateId: "citation.opened_snapshot",
    });
  });

  it("rejects private recipe and verification gate payloads", () => {
    const credentialPrefix = ["sk", "proj"].join("-");
    const privateKeyLiteral = ["private", "key"].join("_");

    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_recipe_selection",
      appliedRecipes: [{
        recipeId: `provider-${"token"}`,
        version: "1.0.0",
        role: "primary",
        governed: true,
      }],
    })).toBeNull();

    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_recipe_selection",
      appliedRecipes: [{
        recipeId: `${credentialPrefix}-abc123`,
        version: "1.0.0",
        role: "primary",
        governed: true,
      }],
    })).toBeNull();

    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_recipe_selection",
      appliedRecipes: [{
        recipeId: `bear${"er"}-abc123`,
        version: "1.0.0",
        role: "primary",
        governed: true,
      }],
    })).toBeNull();

    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_verification_gate",
      gateId: privateKeyLiteral,
      stage: "before_output_projection",
      status: "passed",
      validatorTrustClass: "deterministic",
      reasonCodes: [],
      evidenceRefs: [],
      policyDecisionId: "policy-1",
      checkedAt: 1760000000000,
    })).toBeNull();

    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_verification_gate",
      gateId: "citation.opened_snapshot",
      stage: "before_output_projection",
      status: "passed",
      validatorTrustClass: "deterministic",
      reasonCodes: [`tool-${"args"}`],
      evidenceRefs: [],
      policyDecisionId: "policy-1",
      checkedAt: 1760000000000,
    })).toBeNull();

    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_verification_gate",
      gateId: "citation.opened_snapshot",
      stage: "before_output_projection",
      status: "passed",
      validatorTrustClass: "deterministic",
      reasonCodes: [],
      evidenceRefs: [{
        id: `evidence:sha256:${"2".repeat(64)}`,
        body: "raw-proof",
      }],
      policyDecisionId: "policy-1",
      checkedAt: 1760000000000,
    })).toBeNull();
  });

  it("rejects raw ADK and private payloads", () => {
    expect(parseOpenMagiRuntimeEvent({
      type: "adk_event",
      functionCall: { args: { token: "secret" } },
    })).toBeNull();
    expect(isPublicRuntimeEventPayload({ token: "secret" })).toBe(false);
    expect(isPublicRuntimeEventPayload({ cookie: "abc" })).toBe(false);
    expect(isPublicRuntimeEventPayload({ rawTranscript: "private" })).toBe(false);
    expect(isPublicRuntimeEventPayload({ auth: "private" })).toBe(false);
    expect(isPublicRuntimeEventPayload({ session: "private" })).toBe(false);
    expect(isPublicRuntimeEventPayload({ private: "metadata" })).toBe(false);
    expect(isPublicRuntimeEventPayload({ nested: [{ toolResult: "raw output" }] })).toBe(false);
    expect(parseOpenMagiRuntimeEvent({
      type: "text_delta",
      delta: "ok",
      function_call: { args: { q: "raw" } },
    })).toBeNull();
    expect(parseOpenMagiRuntimeEvent({
      type: "text_delta",
      delta: "ok",
      "google.adk": { event: "raw" },
    })).toBeNull();
    expect(parseOpenMagiRuntimeEvent({
      type: "text_delta",
      delta: "ok",
      googleAdk: { event: "raw" },
    })).toBeNull();
    expect(isPublicRuntimeEventPayload({ transcript: "private" })).toBe(false);
    expect(isPublicRuntimeEventPayload({ googleAdkEvent: { event: "raw" } })).toBe(false);
    expect(isPublicRuntimeEventPayload({ sessionId: "private" })).toBe(false);
    expect(isPublicRuntimeEventPayload({ session_id: "private" })).toBe(false);
    expect(isPublicRuntimeEventPayload({ "session:id": "private" })).toBe(false);
    expect(isPublicRuntimeEventPayload({ authSession: "private" })).toBe(false);
    expect(isPublicRuntimeEventPayload({ tool_results: [{ result: "raw output" }] })).toBe(false);
    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_guardrail",
      guardrailId: "claim-citation-gate",
      stage: "before_output_projection",
      status: "blocked",
      reasonCodes: ["unsupported_claim"],
      policyDecisionId: "policy_decision_01",
      validatorTrustClass: "deterministic",
      evidenceRefs: ["session:private-ref"],
      redactionStatus: "redacted",
    })).toBeNull();
    expect(parseOpenMagiRuntimeEvent({
      type: "deterministic_guardrail",
      guardrailId: "google:adk",
      stage: "before_output_projection",
      status: "blocked",
      reasonCodes: ["tool:args"],
      policyDecisionId: "policy_decision_01",
      validatorTrustClass: "deterministic",
      evidenceRefs: [],
      redactionStatus: "redacted",
    })).toBeNull();
  });

  it("rejects Python ADK private payload attempts and ignores default-off aliases", () => {
    for (const event of pythonAdkPrivatePayloadEvents) {
      expect(parseOpenMagiRuntimeEvent(event)).toBeNull();
      expect(isPublicRuntimeEventPayload(event)).toBe(false);
    }

    for (const event of pythonAdkDefaultOffSurfaceEvents) {
      expect(parseOpenMagiRuntimeEvent(event)).toBeNull();
    }

    const modelFallback = pythonAdkPublicReplayEvents.find((event) => event.type === "model_fallback");
    expect(modelFallback).toBeDefined();
    expect(parseOpenMagiRuntimeEvent(modelFallback)).toBeNull();

    const privateTextDelta = pythonAdkPrivatePayloadEvents.find(
      (event) => event.type === "text_delta",
    );
    expect(privateTextDelta).toBeDefined();
    expect(parseOpenMagiRuntimeEvent(privateTextDelta)).toBeNull();
  });
});
