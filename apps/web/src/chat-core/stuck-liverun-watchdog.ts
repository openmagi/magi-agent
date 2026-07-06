import type { ChannelState } from "./types";

/**
 * Generous no-frame silence window before the client treats a live SSE run as
 * dead and reconciles it to the committed message.
 *
 * chat-proxy injects a `: heartbeat` frame roughly every 15s, so any live turn
 * refreshes `lastFrameAt` at least that often. Crossing this window therefore
 * means ~6 consecutive heartbeats were missed: the SSE connection is gone even
 * though the browser's fetch reader never observed the socket close (the
 * classic half-open-socket case behind the stuck "Processing tool result"
 * symptom, where the backend committed `turn_end` but the terminal frame never
 * reached the client). Tuned generous (design floor was 45s) so a merely slow
 * turn is never reconciled out from under a live stream.
 */
export const STUCK_LIVERUN_SILENCE_MS = 90_000;

type TurnPhase = ChannelState["turnPhase"];

function isTerminalTurnPhase(turnPhase: TurnPhase | undefined): boolean {
  return turnPhase === "committed" || turnPhase === "aborted";
}

export interface StuckLiveRunInput {
  /** True while the reducer still believes a live turn is streaming. */
  streaming: boolean;
  /** True once the client is already polling active-snapshot to recover. */
  reconnecting?: boolean;
  /** Latest structured runtime phase; terminal phases never reconcile. */
  turnPhase?: TurnPhase;
  /** Timestamp (ms) of the last SSE frame of ANY kind, including heartbeat. */
  lastFrameAt?: number | null;
  /** Current wall-clock time (ms). */
  now: number;
  /** Override the silence window (defaults to {@link STUCK_LIVERUN_SILENCE_MS}). */
  silenceMs?: number;
}

/**
 * Pure decision: should the stuck-live-run watchdog reconcile this channel to
 * the committed message?
 *
 * Fires ONLY when a live run has gone silent past the generous window (no SSE
 * frame, not even a heartbeat, so the connection is gone) AND the reducer has
 * neither already settled the turn nor started a recovery poll. Returns false
 * the instant frames are still arriving, the moment a terminal frame settles
 * the turn, or while a recovery poll is already in flight. A normal completing
 * turn flips `streaming` to false before this can fire, so it is unaffected.
 */
export function shouldReconcileStuckLiveRun(input: StuckLiveRunInput): boolean {
  if (!input.streaming) return false;
  if (input.reconnecting === true) return false;
  if (isTerminalTurnPhase(input.turnPhase)) return false;
  const lastFrameAt = input.lastFrameAt;
  if (typeof lastFrameAt !== "number" || !Number.isFinite(lastFrameAt)) {
    return false;
  }
  const silenceMs = input.silenceMs ?? STUCK_LIVERUN_SILENCE_MS;
  return input.now - lastFrameAt >= silenceMs;
}

/**
 * The idle channel-state patch applied when the watchdog reconciles a stuck run
 * to the committed message. Mirrors the settle block the mount-resume and
 * mid-stream-recovery paths already use: flips the live run to idle, drops the
 * truncated streaming bubble (`streamingText` cleared so the committed
 * assistant row, merged separately from the channel-messages fetch, is what
 * renders), and clears every transient "Processing" driver
 * (turnPhase / heartbeat / activeTools / subagents / injections / transcript)
 * so the AGENTS panel chips clear.
 */
export function stuckLiveRunResolvedChannelState(): Partial<ChannelState> {
  return {
    streaming: false,
    streamingText: "",
    thinkingText: "",
    hasTextContent: false,
    reconnecting: false,
    turnPhase: null,
    heartbeatElapsedMs: null,
    pendingInjectionCount: 0,
    activeTools: [],
    subagents: [],
    documentDraft: null,
    liveTranscriptItems: [],
    lastFrameAt: null,
  };
}
