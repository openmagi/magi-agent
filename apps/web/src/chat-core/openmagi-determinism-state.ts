import type { OpenMagiRuntimeEvent } from "./openmagi-runtime-events";
import type {
  AppliedRecipeSummary,
  DeterministicGuardrailSummary,
  DeterministicRuntimeState,
  RecipeSelectionSummary,
  VerificationGateSummary,
} from "./types";

const MAX_GUARDRAILS = 12;
const MAX_VERIFICATION_GATES = 16;

function appendGuardrail(
  existing: DeterministicGuardrailSummary[] | undefined,
  guardrail: DeterministicGuardrailSummary,
): DeterministicGuardrailSummary[] {
  return [...(existing ?? []), guardrail].slice(-MAX_GUARDRAILS);
}

function appendVerificationGate(
  existing: VerificationGateSummary[] | undefined,
  gate: VerificationGateSummary,
): VerificationGateSummary[] {
  return [
    ...(existing ?? []).filter((item) => item.gateId !== gate.gateId),
    gate,
  ].slice(-MAX_VERIFICATION_GATES);
}

function appliedRecipeSummary(
  recipe: Extract<OpenMagiRuntimeEvent, { type: "deterministic_recipe_selection" }>["appliedRecipes"][number],
): AppliedRecipeSummary {
  return { ...recipe };
}

function recipeSelectionSummary(
  event: Extract<OpenMagiRuntimeEvent, { type: "deterministic_recipe_selection" }>,
): RecipeSelectionSummary | undefined {
  if (!event.status) return undefined;
  return {
    status: event.status,
    ...(event.selectionSource ? { selectionSource: event.selectionSource } : {}),
    requestedRecipeRefs: [...(event.requestedRecipeRefs ?? [])],
    appliedRecipeRefs: [...(event.appliedRecipeRefs ?? [])],
    omittedRecipeRefs: [...(event.omittedRecipeRefs ?? [])],
    omissionReasons: [...(event.omissionReasons ?? [])],
    ...(event.policySnapshotDigest ? { policySnapshotDigest: event.policySnapshotDigest } : {}),
    ...(typeof event.turnBlocked === "boolean" ? { turnBlocked: event.turnBlocked } : {}),
    ...(typeof event.fallbackUsed === "boolean" ? { fallbackUsed: event.fallbackUsed } : {}),
    ...(event.nextAction ? { nextAction: event.nextAction } : {}),
  };
}

export function applyDeterministicRuntimeEvent(
  current: DeterministicRuntimeState | undefined,
  event: OpenMagiRuntimeEvent,
): DeterministicRuntimeState | undefined {
  const state = current ?? {};
  switch (event.type) {
    case "deterministic_workflow":
      return {
        ...state,
        workflowId: event.workflowId,
        workflowVersion: event.workflowVersion,
        routeId: event.routeId,
        governed: event.governed,
        effectivePolicySnapshotDigest: event.effectivePolicySnapshotDigest,
        ledgerHeadDigest: event.ledgerHeadDigest,
        checkpointId: event.checkpointId,
      };
    case "deterministic_guardrail":
      return {
        ...state,
        guardrails: appendGuardrail(state.guardrails, {
          guardrailId: event.guardrailId,
          stage: event.stage,
          status: event.status,
          reasonCodes: [...event.reasonCodes],
          validatorTrustClass: event.validatorTrustClass,
          policyDecisionId: event.policyDecisionId,
          evidenceRefs: [...event.evidenceRefs],
        }),
      };
    case "deterministic_projection":
      return {
        ...state,
        projectionMode: event.projectionMode,
        outputAllowed: event.outputAllowed,
        blockedReasonCodes: [...event.blockedReasonCodes],
        claimCount: event.claimCount,
        renderedClaimCount: event.renderedClaimCount,
      };
    case "deterministic_fallback":
      return {
        ...state,
        fallbackReasonCode: event.reasonCode,
        fallbackAuthority: event.toAuthority,
      };
    case "deterministic_recipe_selection":
      return {
        ...state,
        appliedRecipes: event.appliedRecipes.map(appliedRecipeSummary),
        recipeSelection: recipeSelectionSummary(event),
      };
    case "deterministic_verification_gate":
      return {
        ...state,
        verificationGates: appendVerificationGate(state.verificationGates, {
          gateId: event.gateId,
          stage: event.stage,
          status: event.status,
          validatorTrustClass: event.validatorTrustClass,
          reasonCodes: [...event.reasonCodes],
          evidenceRefs: [...event.evidenceRefs],
          ...(event.policyDecisionId ? { policyDecisionId: event.policyDecisionId } : {}),
          ...(typeof event.checkedAt === "number" ? { checkedAt: event.checkedAt } : {}),
        }),
      };
    case "text_delta":
    case "thinking_delta":
    default:
      return current;
  }
}
