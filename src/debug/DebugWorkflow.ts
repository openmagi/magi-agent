export interface DebugTurnState {
  sessionKey: string;
  turnId: string;
  classified: boolean;
  investigated: boolean;
  hypothesized: boolean;
  patched: boolean;
  verified: boolean;
  warnings: string[];
  lastUpdatedAt: number;
}

export interface DebugWorkflowStatus {
  enabled: boolean;
  activeTurns: number;
  latest: Omit<DebugTurnState, "lastUpdatedAt"> | null;
}

const DEBUG_RELEVANT_PATTERNS: readonly RegExp[] = [
  /\b(?:bug|debug|regression|failing|failure|broken|stack trace|root cause|investigat(?:e|ion))\b/i,
  /(?:오류|버그|회귀|실패|고장|원인\s*(?:찾|분석)|디버그)/u,
  /<task_contract>[\s\S]{0,200}<task_type>\s*debug\s*<\/task_type>/i,
];

function now(): number {
  return Date.now();
}

export function isDebugRelevantTurn(userMessage: string): boolean {
  if (!userMessage || !userMessage.trim()) {
    return false;
  }
  return DEBUG_RELEVANT_PATTERNS.some((pattern) => pattern.test(userMessage));
}

export class DebugWorkflow {
  private readonly states = new Map<string, DebugTurnState>();

  private key(sessionKey: string, turnId: string): string {
    return `${sessionKey}::${turnId}`;
  }

  private touch(state: DebugTurnState): DebugTurnState {
    state.lastUpdatedAt = now();
    this.states.set(this.key(state.sessionKey, state.turnId), state);
    return state;
  }

  private ensureState(sessionKey: string, turnId: string): DebugTurnState | null {
    const key = this.key(sessionKey, turnId);
    const existing = this.states.get(key);
    if (existing) {
      return existing;
    }
    return null;
  }

  classifyTurn(sessionKey: string, turnId: string, userMessage: string): DebugTurnState | null {
    if (!isDebugRelevantTurn(userMessage)) {
      return null;
    }
    return this.touch({
      sessionKey,
      turnId,
      classified: true,
      investigated: false,
      hypothesized: false,
      patched: false,
      verified: false,
      warnings: [],
      lastUpdatedAt: now(),
    });
  }

  recordInspection(sessionKey: string, turnId: string, _detail: string): void {
    const state = this.ensureState(sessionKey, turnId);
    if (!state) return;
    state.investigated = true;
    this.touch(state);
  }

  recordHypothesis(sessionKey: string, turnId: string, _detail: string): void {
    const state = this.ensureState(sessionKey, turnId);
    if (!state) return;
    state.hypothesized = true;
    this.touch(state);
  }

  recordPatch(sessionKey: string, turnId: string, _detail: string): void {
    const state = this.ensureState(sessionKey, turnId);
    if (!state) return;
    state.patched = true;
    if (!state.investigated && !state.warnings.includes("patched_before_investigation")) {
      state.warnings.push("patched_before_investigation");
    }
    this.touch(state);
  }

  recordVerification(sessionKey: string, turnId: string, _detail: string): void {
    const state = this.ensureState(sessionKey, turnId);
    if (!state) return;
    state.verified = true;
    this.touch(state);
  }

  getTurnState(sessionKey: string, turnId: string): DebugTurnState | null {
    return this.states.get(this.key(sessionKey, turnId)) ?? null;
  }

  status(): DebugWorkflowStatus {
    let latest: DebugTurnState | null = null;
    for (const state of this.states.values()) {
      if (!latest || state.lastUpdatedAt > latest.lastUpdatedAt) {
        latest = state;
      }
    }
    return {
      enabled: true,
      activeTurns: this.states.size,
      latest: latest
        ? {
            sessionKey: latest.sessionKey,
            turnId: latest.turnId,
            classified: latest.classified,
            investigated: latest.investigated,
            hypothesized: latest.hypothesized,
            patched: latest.patched,
            verified: latest.verified,
            warnings: [...latest.warnings],
          }
        : null,
    };
  }
}
