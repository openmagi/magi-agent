import type { InterruptTurnResult } from "./chat-client";

export interface CancelActiveTurnWithQueueHandoffOptions {
  hasQueued: () => boolean;
  promoteQueuedForHandoff?: () => void;
  cancelStream: (options: { preserveQueue: boolean }) => void;
  interrupt: (handoffRequested: boolean) => Promise<InterruptTurnResult>;
  drainQueue: () => void;
}

export interface CancelActiveTurnWithQueueHandoffResult {
  handoffRequested: boolean;
  interruptAccepted: boolean;
  drained: boolean;
}

export interface EscCancelDecisionInput {
  hasQueued: boolean;
  armedUntil: number | null;
  now: number;
  armWindowMs?: number;
}

export type EscCancelDecision =
  | { action: "arm"; nextArmedUntil: number }
  | { action: "cancel"; nextArmedUntil: null };

const DEFAULT_ESC_ARM_WINDOW_MS = 5_000;

export function buildEscCancelDecision({
  hasQueued,
  armedUntil,
  now,
  armWindowMs = DEFAULT_ESC_ARM_WINDOW_MS,
}: EscCancelDecisionInput): EscCancelDecision {
  if (hasQueued) return { action: "cancel", nextArmedUntil: null };
  if (armedUntil !== null && now <= armedUntil) {
    return { action: "cancel", nextArmedUntil: null };
  }
  return { action: "arm", nextArmedUntil: now + armWindowMs };
}

export async function cancelActiveTurnWithQueueHandoff({
  hasQueued,
  promoteQueuedForHandoff,
  cancelStream,
  interrupt,
  drainQueue,
}: CancelActiveTurnWithQueueHandoffOptions): Promise<CancelActiveTurnWithQueueHandoffResult> {
  const handoffRequested = hasQueued();
  if (handoffRequested) {
    promoteQueuedForHandoff?.();
  }
  const interruptPromise = interrupt(handoffRequested);
  cancelStream({ preserveQueue: handoffRequested });

  const interruptResult = await interruptPromise;
  const canDrain =
    handoffRequested &&
    (interruptResult.accepted || interruptResult.reason === "no_active_turn");

  if (canDrain) {
    drainQueue();
  }

  return {
    handoffRequested,
    interruptAccepted: interruptResult.accepted,
    drained: canDrain,
  };
}
