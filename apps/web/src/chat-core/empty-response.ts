import type { ChannelState } from "./types";

const TRANSIENT_CONNECTION_RETRY_ERROR_RE = /^Connecting to bot\.\.\. \(\d+\/\d+\)$/;
const STALE_TRANSIENT_CONNECTION_MS = 30_000;

export function hasNonTextTurnWork(state: Partial<ChannelState> | null | undefined): boolean {
  if (!state) return false;
  const turnPhase = state.turnPhase ?? null;
  return (
    (turnPhase !== null && turnPhase !== "pending") ||
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

export function isTransientConnectionRetryError(value: string | null | undefined): boolean {
  return typeof value === "string" && TRANSIENT_CONNECTION_RETRY_ERROR_RE.test(value);
}

export function shouldReleaseMissingActiveSnapshot(
  state: Partial<ChannelState> | null | undefined,
  sawSnapshot: boolean,
): boolean {
  if (!state?.streaming) return false;
  if (sawSnapshot) return true;
  if (state.reconnecting) return true;
  return isTransientConnectionRetryError(state.error);
}

export function shouldForceReleaseStaleTransientConnection(
  state: Partial<ChannelState> | null | undefined,
  nowMs: number,
  staleAfterMs = STALE_TRANSIENT_CONNECTION_MS,
): boolean {
  if (!state?.streaming) return false;
  if (!state.reconnecting && !isTransientConnectionRetryError(state.error)) return false;
  if (state.hasTextContent) return false;
  if (state.streamingText?.trim()) return false;
  if (hasNonTextTurnWork(state)) return false;
  const startedAt = typeof state.thinkingStartedAt === "number" ? state.thinkingStartedAt : null;
  if (startedAt === null) return true;
  return nowMs - startedAt >= staleAfterMs;
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
  // An errored turn (e.g. litellm BadRequest, provider auth failure) is a
  // mid-task stop just like the tool-only case below: draining the queue would
  // dispatch every backlogged user message against the SAME broken session,
  // surfacing as the bot replying to an OLDER message — or replying to all
  // queued messages at once. Hold the queue instead and let the user retry.
  if (state?.error) return false;
  return !hasNonTextTurnWork(state);
}
