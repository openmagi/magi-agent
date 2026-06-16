import type { ChannelState, MissionActivity } from "./types";

const TERMINAL_GOAL_STATUSES = new Set<MissionActivity["status"]>([
  "completed",
  "cancelled",
  "failed",
]);

function isMissionStatus(value: unknown): value is MissionActivity["status"] {
  return (
    value === "queued" ||
    value === "running" ||
    value === "blocked" ||
    value === "waiting" ||
    value === "completed" ||
    value === "failed" ||
    value === "cancelled" ||
    value === "paused"
  );
}

function statusFromEvent(eventType: unknown): MissionActivity["status"] | null {
  if (eventType === "blocked") return "blocked";
  if (eventType === "completed") return "completed";
  if (eventType === "failed") return "failed";
  if (eventType === "cancelled" || eventType === "cancel_requested") return "cancelled";
  if (eventType === "paused") return "paused";
  if (eventType === "resumed" || eventType === "unblocked" || eventType === "heartbeat") {
    return "running";
  }
  return null;
}

function activeGoalMissionIdFor(
  state: Partial<ChannelState>,
  mission: MissionActivity,
): string | null {
  if (mission.kind !== "goal") return state.activeGoalMissionId ?? null;
  return TERMINAL_GOAL_STATUSES.has(mission.status) ? null : mission.id;
}

function missionRefreshPatch(
  state: Partial<ChannelState>,
  missionId: string | null,
): Pick<ChannelState, "missionRefreshSeq" | "lastMissionEventMissionId"> {
  return {
    missionRefreshSeq: (state.missionRefreshSeq ?? 0) + 1,
    lastMissionEventMissionId: missionId,
  };
}

export function applyMissionEvent(
  state: Partial<ChannelState>,
  event: Record<string, unknown>,
): Partial<ChannelState> {
  const now = Date.now();
  const missions = [...(state.missions ?? [])];

  if (event.type === "mission_created" && event.mission && typeof event.mission === "object") {
    const mission = event.mission as Record<string, unknown>;
    const id = typeof mission.id === "string" ? mission.id : null;
    const title = typeof mission.title === "string" ? mission.title : null;
    if (!id || !title) return state;
    const kind = typeof mission.kind === "string" ? mission.kind : "manual";
    const status = isMissionStatus(mission.status) ? mission.status : "running";
    const next: MissionActivity = { id, title, kind, status, updatedAt: now };
    const index = missions.findIndex((item) => item.id === id);
    if (index >= 0) missions[index] = { ...missions[index], ...next };
    else missions.unshift(next);
    return {
      ...state,
      missions,
      activeGoalMissionId: activeGoalMissionIdFor(state, next),
      pendingGoalMissionTitle: next.kind === "goal" ? null : state.pendingGoalMissionTitle,
      ...missionRefreshPatch(state, id),
    };
  }

  if (event.type === "mission_event" && typeof event.missionId === "string") {
    const status = statusFromEvent(event.eventType);
    const index = missions.findIndex((item) => item.id === event.missionId);
    if (index < 0) {
      return {
        ...state,
        ...missionRefreshPatch(state, event.missionId),
      };
    }
    const next: MissionActivity = {
      ...missions[index],
      status: status ?? missions[index].status,
      detail: typeof event.message === "string" ? event.message : missions[index].detail,
      updatedAt: now,
    };
    missions[index] = next;
    return {
      ...state,
      missions,
      activeGoalMissionId: activeGoalMissionIdFor(state, next),
      ...missionRefreshPatch(state, event.missionId),
    };
  }

  return state;
}
