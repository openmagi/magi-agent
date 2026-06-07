export type MissionStatus =
  | "queued"
  | "running"
  | "blocked"
  | "waiting"
  | "completed"
  | "failed"
  | "cancelled"
  | "paused";

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

export type MissionEventType =
  | "created"
  | "claimed"
  | "heartbeat"
  | "evidence"
  | "comment"
  | "blocked"
  | "unblocked"
  | "retry_requested"
  | "cancel_requested"
  | "cancelled"
  | "completed"
  | "failed"
  | "delivered"
  | "paused"
  | "resumed";

export interface MissionSummary {
  id: string;
  bot_id: string;
  channel_type: "app" | "telegram" | "discord" | "internal";
  channel_id: string;
  kind: MissionKind;
  title: string;
  summary: string | null;
  status: MissionStatus;
  priority: number;
  created_by: "user" | "agent" | "cron" | "system";
  assignee_profile: string | null;
  parent_mission_id: string | null;
  root_mission_id: string | null;
  used_turns: number;
  budget_turns: number | null;
  last_event_at: string | null;
  completed_at: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface MissionRun {
  id: string;
  mission_id: string;
  bot_id: string;
  trigger_type:
    | "user"
    | "goal_continue"
    | "cron"
    | "script_cron"
    | "retry"
    | "handoff"
    | "resume";
  status: "running" | "completed" | "failed" | "cancelled" | "timed_out";
  session_key: string | null;
  turn_id: string | null;
  spawn_task_id: string | null;
  cron_id: string | null;
  started_at: string;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
  stdout_preview: string | null;
  result_preview: string | null;
  metadata: Record<string, unknown>;
}

export interface MissionEvent {
  id: string;
  mission_id: string;
  run_id: string | null;
  actor_type: "user" | "agent" | "system" | "cron";
  actor_id: string | null;
  event_type: MissionEventType;
  message: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface MissionArtifact {
  id: string;
  mission_id: string;
  run_id: string | null;
  kind:
    | "file"
    | "artifact"
    | "kb_document"
    | "browser_screenshot"
    | "url"
    | "subagent_output"
    | "stdout";
  title: string;
  uri: string | null;
  storage_key: string | null;
  preview: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface MissionDetail {
  mission: MissionSummary;
  runs: MissionRun[];
  events: MissionEvent[];
  artifacts: MissionArtifact[];
}
