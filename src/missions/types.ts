export type MissionKind =
  | "manual"
  | "goal"
  | "spawn"
  | "cron"
  | "script_cron"
  | "pipeline"
  | "browser_qa"
  | "document"
  | "research";

export type MissionChannelType = "app" | "telegram" | "discord" | "internal";

export interface MissionRecord {
  id: string;
  title: string;
  kind: MissionKind;
  status: string;
}

export interface CreateMissionInput {
  channelType: MissionChannelType;
  channelId: string;
  kind: MissionKind;
  title: string;
  summary?: string;
  status?: string;
  createdBy: "user" | "agent" | "cron" | "system";
  idempotencyKey?: string;
  metadata?: Record<string, unknown>;
}

export type MissionActionEventType =
  | "cancel_requested"
  | "retry_requested"
  | "unblocked";

export interface MissionActionEvent {
  id: string;
  mission_id: string;
  run_id?: string | null;
  actor_type?: string;
  actor_id?: string | null;
  event_type: MissionActionEventType;
  message?: string | null;
  payload?: Record<string, unknown>;
  created_at?: string;
}

export interface ListMissionActionEventsInput {
  since?: string;
  limit?: number;
}

export interface RestartRecoveryInput {
  startedAt: string;
  reason?: string;
}

export interface RestartRecoveryResult {
  abandoned: number;
  missionIds: string[];
  resumeRequested?: number;
  resumeMissionIds?: string[];
}

export interface GoalMissionResumeInput {
  actionEventId: string;
  missionId: string;
  startedAt?: string;
  sessionKey: string;
  channel: {
    type: MissionChannelType;
    channelId: string;
  };
  objective: string;
  sourceRequest?: string;
  title?: string;
  completionCriteria: string[];
  turnsUsed: number;
  maxTurns?: number;
  resumeContext?: string;
}
