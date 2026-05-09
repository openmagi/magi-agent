import type { ChannelState, SubagentActivity, TaskBoardSnapshot, ToolActivity } from "./types";

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
  pendingInjectionCount?: number;
  activeTools?: ToolActivity[];
  subagents?: SubagentActivity[];
  taskBoard?: TaskBoardSnapshot | null;
}

export function isLiveActiveSnapshot(
  snapshot: ActiveSnapshot | null | undefined,
): snapshot is ActiveSnapshot {
  if (!snapshot) return false;
  return (
    snapshot.status === "running" ||
    !!snapshot.content ||
    !!snapshot.thinking ||
    !!snapshot.turnPhase ||
    (snapshot.activeTools?.length ?? 0) > 0 ||
    (snapshot.subagents?.length ?? 0) > 0 ||
    !!snapshot.taskBoard?.tasks.length
  );
}

function hasVisibleSnapshotProgress(snapshot: ActiveSnapshot): boolean {
  return (
    !!snapshot.content ||
    !!snapshot.thinking ||
    !!snapshot.turnPhase ||
    snapshot.heartbeatElapsedMs !== undefined ||
    (snapshot.pendingInjectionCount ?? 0) > 0 ||
    (snapshot.activeTools?.length ?? 0) > 0 ||
    (snapshot.subagents?.length ?? 0) > 0 ||
    !!snapshot.taskBoard?.tasks.length
  );
}

export function channelStateFromActiveSnapshot(
  snapshot: ActiveSnapshot,
  existing?: ChannelState,
): Partial<ChannelState> {
  const detached = snapshot.detached === true;
  const activeTools = snapshot.activeTools ?? existing?.activeTools ?? [];
  const subagents = snapshot.subagents ?? existing?.subagents ?? [];
  const taskBoard = snapshot.taskBoard ?? existing?.taskBoard ?? null;
  const hasProgress =
    hasVisibleSnapshotProgress(snapshot) ||
    activeTools.length > 0 ||
    subagents.length > 0 ||
    !!taskBoard?.tasks.length;
  return {
    streaming: !detached,
    streamingText: snapshot.content ?? "",
    thinkingText: snapshot.thinking ?? "",
    hasTextContent: !!snapshot.content,
    thinkingStartedAt: existing?.thinkingStartedAt ?? snapshot.startedAt ?? null,
    reconnecting: !detached && !hasProgress,
    error: null,
    turnPhase: detached ? null : (snapshot.turnPhase ?? existing?.turnPhase ?? "pending"),
    heartbeatElapsedMs: detached
      ? null
      : (snapshot.heartbeatElapsedMs ?? existing?.heartbeatElapsedMs ?? null),
    pendingInjectionCount:
      detached ? 0 : (snapshot.pendingInjectionCount ?? existing?.pendingInjectionCount ?? 0),
    activeTools,
    subagents,
    taskBoard,
  };
}
