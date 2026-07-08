// Pure bridge: project the streaming-stack `StreamChatState` (produced by the
// SSE reducer) onto the legacy presentation contract `ChannelState` consumed by
// the shared `ChatMessages` + `KbSidePanel` components. NO React, NO fetch.
//
// This lets the new transport/data layer drive the SAME legacy presentation:
// the live assistant bubble + thinking block (center, via ChatMessages) and the
// Work tab tool feed (right, via KbSidePanel) both read from `ChannelState`.

import { deriveCitationRepairStatus } from "./citation-repair-status";
import type {
  StreamChatState,
  ToolCardState,
} from "./stream-chat-reducer";
import type { ChannelState, ToolActivity } from "./types";

/** Legal `ChannelState.turnPhase` values (subset of the runtime phase space). */
type TurnPhase = NonNullable<ChannelState["turnPhase"]>;

const TURN_PHASE_VALUES: ReadonlySet<TurnPhase> = new Set<TurnPhase>([
  "pending",
  "planning",
  "executing",
  "verifying",
  "committing",
  "compacting",
  "committed",
  "aborted",
]);

/**
 * Map an arbitrary runtime phase string onto the strict `ChannelState.turnPhase`
 * enum. Direct hits pass through; common runtime synonyms are normalized; an
 * unrecognized phase yields `null` (no phase chrome rather than a wrong one).
 */
function mapTurnPhase(phase: string | null | undefined): TurnPhase | null {
  if (!phase) return null;
  const lower = phase.toLowerCase();
  if (TURN_PHASE_VALUES.has(lower as TurnPhase)) return lower as TurnPhase;
  // Runtime synonyms → canonical UI phases.
  switch (lower) {
    case "prepare":
    case "preparing":
    case "starting":
    case "started":
      return "pending";
    case "plan":
      return "planning";
    case "execute":
    case "executing_tools":
    case "running":
      return "executing";
    case "verify":
    case "verification":
      return "verifying";
    case "commit":
      return "committing";
    case "compact":
      return "compacting";
    case "complete":
    case "completed":
    case "done":
      return "committed";
    case "abort":
    case "error":
    case "failed":
      return "aborted";
    default:
      return null;
  }
}

/** Map a reducer `ToolCardState.status` onto the `ToolActivity` status enum. */
function mapToolStatus(card: ToolCardState): ToolActivity["status"] {
  if (card.rejected) {
    // Distinguish a denied/blocked tool from a generic error for the Work feed.
    const s = card.status.toLowerCase();
    if (
      s === "denied" ||
      s === "blocked" ||
      s === "needs_approval" ||
      s === "rejected"
    ) {
      return "denied";
    }
    return "error";
  }
  const s = card.status.toLowerCase();
  if (s === "running" || s === "in_progress" || s === "pending") return "running";
  if (s === "error" || s === "failed") return "error";
  if (s === "denied" || s === "blocked") return "denied";
  return "done";
}

/** Project one reducer tool card onto the legacy `ToolActivity` shape. */
function toToolActivity(card: ToolCardState, index: number): ToolActivity {
  return {
    id: card.id,
    label: card.name || `tool-${index}`,
    status: mapToolStatus(card),
    startedAt: 0,
    ...(card.inputPreview ? { inputPreview: card.inputPreview } : {}),
    ...(card.outputPreview !== null ? { outputPreview: card.outputPreview } : {}),
    ...(card.durationMs !== null ? { durationMs: card.durationMs } : {}),
  };
}

/**
 * Bridge `StreamChatState` → `ChannelState`.
 *
 * Mapped fields:
 *   - streaming      ← state.streaming
 *   - streamingText  ← state.assistantText (the live assistant bubble text)
 *   - thinkingText   ← state.thinkingText
 *   - hasTextContent ← !!state.assistantText
 *   - turnPhase      ← mapTurnPhase(state.phase.phase)
 *   - activeTools    ← state.tools.values() projected to ToolActivity[]
 *   - taskBoard      ← latest public task_board snapshot
 *   - subagents      ← child lifecycle map projected to SubagentActivity[]
 *   - runtimeTraces  ← runtime_trace/control-event verifier traces
 *   - heartbeatElapsedMs ← runtime/model progress elapsed time
 *   - error          ← null (errors are surfaced separately by the container)
 *
 * All other `ChannelState` fields default to the empty/inert shape so the
 * Work/Missions tabs render without partial-state crashes. Missions remain
 * outside this streaming reducer and default to an empty array.
 */
export function streamStateToChannelState(
  state: StreamChatState,
): ChannelState {
  const activeTools = Array.from(state.tools.values()).map(toToolActivity);
  return {
    streaming: state.streaming,
    streamingText: state.assistantText,
    thinkingText: state.thinkingText,
    error: null,
    hasTextContent: state.assistantText.length > 0,
    thinkingStartedAt: null,
    turnPhase: mapTurnPhase(state.phase?.phase),
    heartbeatElapsedMs: state.heartbeatElapsedMs,
    currentGoal: null,
    pendingInjectionCount: 0,
    activeTools,
    browserFrame: null,
    documentDraft: null,
    subagents: Array.from(state.subagents.values()),
    subagentProgress: {},
    taskBoard: state.taskBoard,
    missions: [],
    activeGoalMissionId: null,
    missionRefreshSeq: 0,
    lastMissionEventMissionId: null,
    pendingGoalMissionTitle: null,
    inspectedSources: state.inspectedSources,
    citationGate: state.citationGate,
    citationRepair: deriveCitationRepairStatus({
      streaming: state.streaming,
      hasAssistantText: state.assistantText.length > 0,
      phase: state.phase?.phase,
      status: state.phase?.status,
    }),
    turnCitations: state.terminal?.citations ?? null,
    runtimeTraces: state.runtimeTraces,
    // Ordered interleaved segments. Tool segment ids reference `activeTools` by
    // the same reducer tool-card id, so the interleaved renderer can look each
    // tool up in the sibling activity list.
    segments: state.segments,
    liveTranscriptItems: [],
    fileProcessing: false,
    reconnecting: false,
    saveError: null,
  };
}
