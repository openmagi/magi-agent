import { AppError } from "@/lib/errors";
import type { MissionDetail, MissionEventType, MissionSummary } from "./types";

const MAX_MESSAGE_CHARS = 2000;

type MissionActionEventType = Extract<
  MissionEventType,
  "comment" | "retry_requested" | "cancel_requested" | "unblocked"
>;

type MissionActionValidationResult =
  | { ok: true }
  | { ok: false; status: 400 | 409; message: string };

export type MissionSupabaseClient = {
  from: (table: string) => unknown;
};

type SingleMissionQuery = {
  select: (columns: string) => {
    eq: (column: string, value: string) => {
      eq: (column: string, value: string) => {
        single: () => Promise<{ data: MissionSummary | null; error: unknown }>;
      };
    };
  };
};

type OrderedMissionQuery<T> = {
  select: (columns: string) => {
    eq: (column: string, value: string) => {
      order: (
        column: string,
        opts: { ascending: boolean },
      ) => Promise<{ data: T[] | null; error?: unknown }>;
    };
  };
};

export function sanitizeMissionMessage(message: unknown): string | null {
  if (typeof message !== "string") return null;
  const trimmed = message.trim();
  if (!trimmed) return null;
  return trimmed.length > MAX_MESSAGE_CHARS
    ? trimmed.slice(0, MAX_MESSAGE_CHARS)
    : trimmed;
}

export function buildMissionActionEvent(input: {
  missionId: string;
  actorId: string;
  eventType: MissionActionEventType;
  message?: string | null;
  payload?: Record<string, unknown>;
}) {
  return {
    mission_id: input.missionId,
    actor_type: "user" as const,
    actor_id: input.actorId,
    event_type: input.eventType,
    message: sanitizeMissionMessage(input.message),
    payload: input.payload ?? {},
  };
}

export function buildMissionActionPayload(input: {
  mission: MissionSummary;
  eventType: MissionActionEventType;
  message?: string | null;
}): Record<string, unknown> | undefined {
  if (input.eventType === "cancel_requested") {
    const userReason = sanitizeMissionMessage(input.message);
    return {
      reason: "mission_cancel_requested",
      ...(userReason ? { userReason } : {}),
    };
  }

  if (input.mission.kind !== "goal") return undefined;
  const eventType = input.eventType;
  if (eventType !== "retry_requested" && eventType !== "unblocked") {
    return undefined;
  }

  const goal = buildGoalActionPayload({
    mission: input.mission,
    eventType,
    message: input.message,
  });
  if (!goal) return undefined;
  return {
    reason: input.eventType === "retry_requested" ? "manual_retry" : "user_unblocked",
    goal,
  };
}

function buildGoalActionPayload(input: {
  mission: MissionSummary;
  eventType: "retry_requested" | "unblocked";
  message?: string | null;
}): Record<string, unknown> | undefined {
  const { mission } = input;
  const metadata = mission.metadata ?? {};
  const sessionKey = metadataString(metadata, "sessionKey");
  const objective = metadataString(metadata, "objective") ?? stringValue(mission.summary);
  if (!sessionKey || !objective) return undefined;

  const sourceRequest = metadataString(metadata, "sourceRequest") ?? objective;
  const completionCriteria = metadataStringArray(metadata, "completionCriteria");
  const turnsUsed = metadataNumber(metadata, "turnsUsed")
    ?? finiteNumber(mission.used_turns)
    ?? 0;
  const maxTurns = metadataNumber(metadata, "maxTurns")
    ?? finiteNumber(mission.budget_turns);
  const message = sanitizeMissionMessage(input.message);
  const resumeContext = message
    ? input.eventType === "retry_requested"
      ? `User requested retry: ${message}`
      : `User unblocked mission: ${message}`
    : undefined;

  return {
    sessionKey,
    channelType: mission.channel_type,
    channelId: mission.channel_id,
    objective,
    sourceRequest,
    title: mission.title,
    completionCriteria,
    turnsUsed,
    ...(maxTurns !== undefined ? { maxTurns } : {}),
    ...(resumeContext ? { resumeContext } : {}),
  };
}

export function validateMissionAction(input: {
  mission: MissionSummary;
  eventType: MissionActionEventType;
  message?: string | null;
}): MissionActionValidationResult {
  const status = input.mission.status;
  if (input.eventType === "comment") return { ok: true };

  if (input.eventType === "retry_requested") {
    if (status === "failed" || status === "cancelled" || isInputWaitingStatus(status)) {
      return { ok: true };
    }
    return invalidAction("retried", status);
  }

  if (input.eventType === "unblocked") {
    if (isInputWaitingStatus(status)) return { ok: true };
    return invalidAction("unblocked", status);
  }

  if (input.eventType === "cancel_requested") {
    if (
      status === "queued" ||
      status === "running" ||
      isInputWaitingStatus(status)
    ) {
      return { ok: true };
    }
    return invalidAction("cancelled", status);
  }

  return { ok: true };
}

function isInputWaitingStatus(status: MissionSummary["status"]): boolean {
  return status === "blocked" || status === "waiting" || status === "paused";
}

function invalidAction(
  verb: "retried" | "unblocked" | "cancelled",
  status: MissionSummary["status"],
): MissionActionValidationResult {
  return {
    ok: false,
    status: 409,
    message: `Mission cannot be ${verb} while ${status}`,
  };
}

export async function assertMissionBelongsToBot(
  supabase: MissionSupabaseClient,
  missionId: string,
  botId: string,
): Promise<MissionSummary> {
  const query = (supabase.from("agent_missions") as SingleMissionQuery)
    .select("*")
    .eq("id", missionId)
    .eq("bot_id", botId);
  const { data, error } = await query.single();
  if (error || !data) throw new AppError("Mission not found", 404);
  return data;
}

export async function loadMissionDetail(
  supabase: MissionSupabaseClient,
  mission: MissionSummary,
): Promise<MissionDetail> {
  const [runs, events, artifacts] = await Promise.all([
    (supabase.from("agent_mission_runs") as OrderedMissionQuery<MissionDetail["runs"][number]>)
      .select("*")
      .eq("mission_id", mission.id)
      .order("started_at", { ascending: false }),
    (supabase.from("agent_mission_events") as OrderedMissionQuery<MissionDetail["events"][number]>)
      .select("*")
      .eq("mission_id", mission.id)
      .order("created_at", { ascending: true }),
    (supabase.from("agent_mission_artifacts") as OrderedMissionQuery<MissionDetail["artifacts"][number]>)
      .select("*")
      .eq("mission_id", mission.id)
      .order("created_at", { ascending: true }),
  ]);

  return {
    mission,
    runs: runs.data ?? [],
    events: events.data ?? [],
    artifacts: artifacts.data ?? [],
  };
}

function metadataString(metadata: Record<string, unknown>, key: string): string | undefined {
  return stringValue(metadata[key]);
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0
    ? value.trim()
    : undefined;
}

function metadataNumber(metadata: Record<string, unknown>, key: string): number | undefined {
  return finiteNumber(metadata[key]);
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

function metadataStringArray(metadata: Record<string, unknown>, key: string): string[] {
  const value = metadata[key];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
}
