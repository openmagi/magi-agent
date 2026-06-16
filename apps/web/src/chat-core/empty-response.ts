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
