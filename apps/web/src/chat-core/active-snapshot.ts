import type {
  ChannelState,
  ChatMessage,
  CitationGateStatus,
  InspectedSource,
  LiveTranscriptItem,
  MissionActivity,
  SubagentActivity,
  TaskBoardSnapshot,
  ToolActivity,
} from "./types";
import { replaceLiveTranscriptText } from "./live-transcript";

export interface ActiveSnapshot {
  turnId: string | null;
  status?: "running";
  /** True when the parent stream is done but background work is still visible. */
  detached?: boolean;
  content: string;
  thinking: string;
  startedAt: number | null;
  updatedAt: number | null;
  turnPhase?: ChannelState["turnPhase"];
  heartbeatElapsedMs?: number | null;
  currentGoal?: string | null;
  pendingInjectionCount?: number;
  activeTools?: ToolActivity[];
  subagents?: SubagentActivity[];
  taskBoard?: TaskBoardSnapshot | null;
  missions?: MissionActivity[];
  activeGoalMissionId?: string | null;
  inspectedSources?: InspectedSource[];
  citationGate?: CitationGateStatus | null;
}

function isTerminalTurnPhase(turnPhase: ChannelState["turnPhase"] | undefined): boolean {
  return turnPhase === "aborted" || turnPhase === "committed";
}

function isFiniteTimestamp(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function normalizeResetBoundaryTimestamp(value?: number | null): number | null {
  return isFiniteTimestamp(value) && value > 0 ? value : null;
}

function isSessionResetDivider(message: ChatMessage): boolean {
  if (message.role !== "system") return false;
  const content = typeof message.content === "string" ? message.content.toLowerCase() : "";
  return message.id.startsWith("system-reset-") || content.includes("session ended");
}

function latestResetTimestamp(
  messages: readonly ChatMessage[],
  resetBoundaryTimestamp?: number | null,
): number | null {
  let latest = normalizeResetBoundaryTimestamp(resetBoundaryTimestamp);
  for (const message of messages) {
    if (!isSessionResetDivider(message) || !isFiniteTimestamp(message.timestamp)) continue;
    if (latest === null || message.timestamp > latest) latest = message.timestamp;
  }
  return latest;
}

function hasPostResetVisibleTurn(messages: readonly ChatMessage[], resetAt: number): boolean {
  return messages.some((message) => (
    (message.role === "user" || message.role === "assistant") &&
    isFiniteTimestamp(message.timestamp) &&
    message.timestamp > resetAt
  ));
}

export function shouldApplyActiveSnapshotAfterReset(
  snapshot: ActiveSnapshot,
  messages: readonly ChatMessage[],
  resetBoundaryTimestamp?: number | null,
): boolean {
  const resetAt = latestResetTimestamp(messages, resetBoundaryTimestamp);
  if (resetAt === null) return true;

  if (isFiniteTimestamp(snapshot.startedAt) && snapshot.startedAt <= resetAt) {
    return false;
  }
  if (
    !isFiniteTimestamp(snapshot.startedAt) &&
    isFiniteTimestamp(snapshot.updatedAt) &&
    snapshot.updatedAt <= resetAt
  ) {
    return false;
  }

  return hasPostResetVisibleTurn(messages, resetAt);
}

/**
 * Reconnect authority: the chat-proxy Redis snapshot is authoritative only for
 * COLD hydration (page refresh / tab switch with no open stream). While a live
 * SSE stream is rendering, that stream + the server's control_replay own the
 * turn, so hydrating from the snapshot would double-render and trigger the
 * U+FFFD repair loop. Never hydrate over an open stream.
 */
export function shouldHydrateFromSnapshot(
  snapshot: ActiveSnapshot,
  messages: readonly ChatMessage[],
  opts: { streamOpen: boolean; resetBoundaryTimestamp?: number | null },
): boolean {
  if (opts.streamOpen) return false;
  return shouldApplyActiveSnapshotAfterReset(
    snapshot,
    messages,
    opts.resetBoundaryTimestamp,
  );
}

export function isLiveActiveSnapshot(
  snapshot: ActiveSnapshot | null | undefined,
): snapshot is ActiveSnapshot {
  if (!snapshot) return false;
  if (snapshot.detached !== true && isTerminalTurnPhase(snapshot.turnPhase)) return false;
  return (
    snapshot.status === "running" ||
    !!snapshot.content ||
    !!snapshot.thinking ||
    !!snapshot.turnPhase ||
    (snapshot.activeTools?.length ?? 0) > 0 ||
    (snapshot.subagents?.length ?? 0) > 0 ||
    (snapshot.missions?.length ?? 0) > 0 ||
    (snapshot.inspectedSources?.length ?? 0) > 0 ||
    !!snapshot.citationGate ||
    !!snapshot.taskBoard?.tasks.length
  );
}

function hasMeaningfulTurnPhase(turnPhase: ChannelState["turnPhase"] | undefined): boolean {
  return (
    turnPhase !== undefined &&
    turnPhase !== null &&
    turnPhase !== "pending" &&
    !isTerminalTurnPhase(turnPhase)
  );
}

function hasMeaningfulSnapshotWork(snapshot: ActiveSnapshot): boolean {
  return (
    !!snapshot.content ||
    !!snapshot.thinking ||
    hasMeaningfulTurnPhase(snapshot.turnPhase) ||
    (snapshot.pendingInjectionCount ?? 0) > 0 ||
    (snapshot.activeTools?.length ?? 0) > 0 ||
    (snapshot.subagents?.length ?? 0) > 0 ||
    (snapshot.missions?.length ?? 0) > 0 ||
    (snapshot.inspectedSources?.length ?? 0) > 0 ||
    !!snapshot.citationGate ||
    !!snapshot.taskBoard?.tasks.length
  );
}

export function shouldReleaseStaleEmptyActiveSnapshot(
  snapshot: ActiveSnapshot | null | undefined,
  nowMs: number,
  staleAfterMs = 30_000,
): boolean {
  if (!snapshot) return false;
  if (snapshot.detached === true) return false;
  if (snapshot.status !== "running") return false;
  if (hasMeaningfulSnapshotWork(snapshot)) return false;
  if (
    typeof snapshot.heartbeatElapsedMs === "number" &&
    Number.isFinite(snapshot.heartbeatElapsedMs) &&
    snapshot.heartbeatElapsedMs < staleAfterMs
  ) {
    return false;
  }
  const lastSeenAt =
    typeof snapshot.updatedAt === "number"
      ? snapshot.updatedAt
      : typeof snapshot.startedAt === "number"
        ? snapshot.startedAt
        : null;
  if (lastSeenAt === null) return true;
  return nowMs - lastSeenAt >= staleAfterMs;
}

function hasVisibleSnapshotProgress(snapshot: ActiveSnapshot): boolean {
  return (
    !!snapshot.content ||
    !!snapshot.thinking ||
    hasMeaningfulTurnPhase(snapshot.turnPhase) ||
    snapshot.heartbeatElapsedMs !== undefined ||
    (snapshot.pendingInjectionCount ?? 0) > 0 ||
    (snapshot.activeTools?.length ?? 0) > 0 ||
    (snapshot.subagents?.length ?? 0) > 0 ||
    (snapshot.missions?.length ?? 0) > 0 ||
    (snapshot.inspectedSources?.length ?? 0) > 0 ||
    !!snapshot.citationGate ||
    !!snapshot.taskBoard?.tasks.length
  );
}

function additiveSnapshotText(snapshotContent: string, existingContent: string): string {
  if (!snapshotContent) return existingContent;
  if (!existingContent) return snapshotContent;
  if (snapshotContent.startsWith(existingContent)) return snapshotContent;
  if (existingContent.startsWith(snapshotContent)) return existingContent;
  return snapshotContent.length > existingContent.length ? snapshotContent : existingContent;
}

function liveTranscriptItemsForSnapshotText(
  content: string,
  existingItems: LiveTranscriptItem[] | undefined,
  receivedAt: number,
): LiveTranscriptItem[] {
  const workItems = (existingItems ?? []).filter((item) => item.kind !== "text");
  if (!content) return workItems;
  return [
    ...workItems,
    ...replaceLiveTranscriptText(content, receivedAt),
  ];
}

export function channelStateFromActiveSnapshot(
  snapshot: ActiveSnapshot,
  existing?: ChannelState,
): Partial<ChannelState> {
  const detached = snapshot.detached === true;
  const streamingText = detached
    ? ""
    : additiveSnapshotText(snapshot.content ?? "", existing?.streamingText ?? "");
  const snapshotReceivedAt = snapshot.updatedAt ?? Date.now();
  const activeTools = snapshot.activeTools ?? existing?.activeTools ?? [];
  const subagents = snapshot.subagents ?? existing?.subagents ?? [];
  const taskBoard = snapshot.taskBoard ?? existing?.taskBoard ?? null;
  const missions = snapshot.missions ?? existing?.missions ?? [];
  const inspectedSources = snapshot.inspectedSources ?? existing?.inspectedSources ?? [];
  const citationGate = snapshot.citationGate ?? existing?.citationGate ?? null;
  const hasProgress =
    hasVisibleSnapshotProgress(snapshot) ||
    activeTools.length > 0 ||
    subagents.length > 0 ||
    missions.length > 0 ||
    inspectedSources.length > 0 ||
    !!citationGate ||
    !!taskBoard?.tasks.length;
  return {
    streaming: !detached,
    streamingText,
    thinkingText: snapshot.thinking ?? "",
    hasTextContent: !!streamingText,
    thinkingStartedAt: existing?.thinkingStartedAt ?? snapshot.startedAt ?? null,
    reconnecting: !detached && !hasProgress,
    error: null,
    turnPhase: detached ? null : (snapshot.turnPhase ?? existing?.turnPhase ?? "pending"),
    heartbeatElapsedMs: detached
      ? null
      : (snapshot.heartbeatElapsedMs ?? existing?.heartbeatElapsedMs ?? null),
    currentGoal: snapshot.currentGoal ?? existing?.currentGoal ?? null,
    pendingInjectionCount:
      detached ? 0 : (snapshot.pendingInjectionCount ?? existing?.pendingInjectionCount ?? 0),
    liveTranscriptItems: liveTranscriptItemsForSnapshotText(
      streamingText,
      existing?.liveTranscriptItems,
      snapshotReceivedAt,
    ),
    activeTools,
    subagents,
    taskBoard,
    missions,
    activeGoalMissionId:
      snapshot.activeGoalMissionId ?? existing?.activeGoalMissionId ?? null,
    inspectedSources,
    citationGate,
  };
}
