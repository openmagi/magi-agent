export interface AcceptedStreamingInjection {
  id: string;
  content: string;
  queuedAt: number;
  drained: boolean;
}

export type AcceptedInjectionState = Record<string, AcceptedStreamingInjection[] | undefined>;

export function recordAcceptedInjection(
  state: AcceptedInjectionState,
  channel: string,
  injection: AcceptedStreamingInjection,
): AcceptedInjectionState {
  return {
    ...state,
    [channel]: [...(state[channel] ?? []), injection],
  };
}

export function markAcceptedInjectionsDrained(
  state: AcceptedInjectionState,
  channel: string,
): AcceptedInjectionState {
  const existing = state[channel];
  if (!existing || existing.length === 0) return state;
  return {
    ...state,
    [channel]: existing.map((item) => ({ ...item, drained: true })),
  };
}

export function consumeAcceptedInjections(
  state: AcceptedInjectionState,
  channel: string,
): {
  next: AcceptedInjectionState;
  consumed: AcceptedStreamingInjection[];
  unresolved: AcceptedStreamingInjection[];
} {
  const consumed = state[channel] ?? [];
  const next = { ...state };
  delete next[channel];
  return {
    next,
    consumed,
    unresolved: consumed.filter((item) => !item.drained),
  };
}
