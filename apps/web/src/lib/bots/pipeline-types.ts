export type PipelineStatus = "in_progress" | "completed" | "paused";

export type PipelineEventName =
  | "pipeline_started"
  | "step_spawned"
  | "step_verified"
  | "step_phantom_detected"
  | "step_stalled"
  | "step_completed"
  | "step_failed"
  | "pipeline_completed"
  | "pipeline_paused"
  | "delivery_error";

export interface PipelineEvent {
  ts: number;
  event: PipelineEventName;
  stepId: string | null;
  details: string | null;
}

export interface PipelineMeta {
  status: PipelineStatus;
  started_at: number;
  updated_at: number;
  completed_at?: number;
  paused_at?: number;
  last_event: PipelineEventName;
  last_step_id?: string;
  severe_count: number;
}

export interface PipelineListItem extends PipelineMeta {
  pipeline_id: string;
}

export interface PipelineSnapshot {
  pipeline_id: string;
  meta: PipelineMeta | null;
  events: PipelineEvent[];
}

export interface Intervention {
  action: "retry_step" | "cancel" | "pause" | "resume";
  step_id: string | null;
  reason: string | null;
  requested_at: number;
}

const SEVERE = new Set<PipelineEventName>([
  "step_phantom_detected", "step_stalled", "step_failed",
  "pipeline_paused", "delivery_error",
]);
const SUCCESS = new Set<PipelineEventName>([
  "step_verified", "step_completed", "pipeline_completed",
]);

export function eventSeverity(ev: PipelineEventName): "success" | "severe" | "info" {
  if (SEVERE.has(ev)) return "severe";
  if (SUCCESS.has(ev)) return "success";
  return "info";
}

export function formatEventName(ev: PipelineEventName): string {
  return ev.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function pipelineDisplayName(pipelineId: string): string {
  // pipeline-20260419-153022 → "Apr 19, 15:30:22"
  const match = /^pipeline-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(pipelineId);
  if (!match) return pipelineId;
  const [, , mm, dd, hh, min, ss] = match;
  const monthName = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][parseInt(mm, 10) - 1] ?? mm;
  return `${monthName} ${parseInt(dd, 10)}, ${hh}:${min}:${ss}`;
}
