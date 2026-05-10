import type { ChannelState } from "./types";

export function hasNonTextTurnWork(state: ChannelState): boolean {
  return (
    !!state.thinkingText ||
    (state.activeTools?.length ?? 0) > 0 ||
    !!state.browserFrame ||
    (state.subagents?.length ?? 0) > 0 ||
    (state.missions?.length ?? 0) > 0 ||
    !!state.taskBoard?.tasks.length ||
    (state.inspectedSources?.length ?? 0) > 0 ||
    !!state.citationGate ||
    (state.pendingInjectionCount ?? 0) > 0
  );
}

export function shouldRetryEmptyCompletion(
  state: ChannelState,
  retryCount: number,
  maxRetries: number,
): boolean {
  if (retryCount >= maxRetries) return false;
  if (state.hasTextContent || state.streamingText.trim().length > 0) return false;
  return !hasNonTextTurnWork(state);
}
