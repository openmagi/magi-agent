import type { ChannelState } from "./types";

export function hasNonTextTurnWork(state: Partial<ChannelState> | null | undefined): boolean {
  if (!state) return false;
  return (
    !!state.thinkingText?.trim() ||
    (state.activeTools?.length ?? 0) > 0 ||
    !!state.browserFrame ||
    !!state.documentDraft ||
    (state.subagents?.length ?? 0) > 0 ||
    (state.missions?.length ?? 0) > 0 ||
    (state.taskBoard?.tasks.length ?? 0) > 0 ||
    (state.inspectedSources?.length ?? 0) > 0 ||
    !!state.citationGate ||
    (state.runtimeTraces?.length ?? 0) > 0 ||
    (state.pendingInjectionCount ?? 0) > 0
  );
}

export function shouldRetryEmptyCompletion(
  state: Partial<ChannelState> | null | undefined,
  retryCount: number,
  maxRetries: number,
): boolean {
  if (retryCount >= maxRetries) return false;
  if (state?.hasTextContent) return false;
  if (state?.streamingText?.trim()) return false;
  return !hasNonTextTurnWork(state);
}

/**
 * Decide whether to drain the next queued message after a turn finalizes.
 *
 * Drain only when the turn actually produced a final answer (or was a truly
 * empty turn with no work, so there is nothing to continue). A turn that ended
 * with work in progress but no final answer text is a mid-task stop — draining
 * would feed the next (newer) queued message into the SAME unfinished backend
 * task, so the old work surfaces as a reply to the new message. Hold the queue
 * instead and let the user retry / the run continue.
 */
export function shouldDrainQueueAfterTurn(
  state: Partial<ChannelState> | null | undefined,
): boolean {
  if (state?.streamingText?.trim()) return true;
  if (state?.hasTextContent) return true;
  return !hasNonTextTurnWork(state);
}
