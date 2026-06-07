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
