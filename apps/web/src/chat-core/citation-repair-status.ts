import type { CitationRepairKind } from "./types";

/**
 * Map the driver's `turn_phase` citation status string onto the UI repair kind.
 *
 * The driver tags a mid-turn citation intervention with `phase="verifying"` and
 * a `status` of `citation_attribution` (re-generate with `[src_N]` markers) or
 * `citation_induce_search` (force a grounding search first). Any other status
 * (or a non-verifying phase) is not a citation intervention and yields null, so
 * normal turns and other verifications never surface the affordance.
 */
export function citationRepairKindFromStatus(
  phase: string | null | undefined,
  status: string | null | undefined,
): CitationRepairKind | null {
  if (phase !== "verifying") return null;
  if (status === "citation_attribution") return "attribution";
  if (status === "citation_induce_search") return "induce_search";
  return null;
}

/**
 * Derive the active in-flight citation-repair affordance for the current turn.
 *
 * Active only while the turn is streaming AND no answer text has yet arrived:
 * once the repaired (or grounded) answer streams the affordance clears
 * naturally, avoiding a stale label lingering over the final answer. Returns
 * null when the turn is not streaming, when an answer is already on the wire,
 * or when the phase/status is not a citation intervention.
 */
export function deriveCitationRepairStatus(input: {
  streaming: boolean;
  hasAssistantText: boolean;
  phase: string | null | undefined;
  status: string | null | undefined;
}): CitationRepairKind | null {
  if (!input.streaming) return null;
  if (input.hasAssistantText) return null;
  return citationRepairKindFromStatus(input.phase, input.status);
}
