import type { MissionActivity } from "./types";
import type { MissionStatus, MissionSummary } from "@/lib/missions/types";

export type MissionWorkQueueFilter = "active" | "needs_input" | "done" | "all";
export type MissionWorkQueueSectionKind = "needs_input" | "running" | "done";
export type MissionWorkQueueAction = "retry" | "cancel" | "unblock";

export interface MissionWorkQueueRow {
  id: string;
  title: string;
  kind: string;
  status: MissionStatus;
  bucket: MissionWorkQueueSectionKind;
  detail?: string;
  summary?: string;
  updatedAt: string | number;
  updatedAtMs: number;
  updatedLabel: string;
  usedTurns: number;
  budgetTurns: number | null;
  action: MissionWorkQueueAction | null;
}

export interface MissionWorkQueueSection {
  kind: MissionWorkQueueSectionKind;
  label: string;
  rows: MissionWorkQueueRow[];
}

export interface MissionWorkQueueModel {
  rows: MissionWorkQueueRow[];
  sections: MissionWorkQueueSection[];
  counts: {
    active: number;
    needsInput: number;
    done: number;
    all: number;
  };
  activeGoal: MissionWorkQueueRow | null;
}

export interface BuildMissionWorkQueueInput {
  summaries: MissionSummary[];
  liveMissions: MissionActivity[];
  filter: MissionWorkQueueFilter;
  query: string;
  activeGoalMissionId?: string | null;
  now?: number;
}

const SECTION_LABELS: Record<MissionWorkQueueSectionKind, string> = {
  needs_input: "Needs input",
  running: "Running",
  done: "Done",
};

const SECTION_RANK: Record<MissionWorkQueueSectionKind, number> = {
  needs_input: 0,
  running: 1,
  done: 2,
};

export function missionStatusBucket(status: MissionStatus): MissionWorkQueueSectionKind {
  if (status === "blocked" || status === "waiting" || status === "paused") {
    return "needs_input";
  }
  if (status === "completed" || status === "failed" || status === "cancelled") {
    return "done";
  }
  return "running";
}

export function missionActionForStatus(status: MissionStatus): MissionWorkQueueAction | null {
  if (status === "blocked" || status === "waiting" || status === "paused") return "unblock";
  if (status === "failed" || status === "cancelled") return "retry";
  if (status === "queued" || status === "running") return "cancel";
  return null;
}

export function formatMissionRelativeTime(
  value: string | number,
  now = Date.now(),
): string {
  const timestamp = timestampFor(value);
  if (!Number.isFinite(timestamp)) return "recent";
  const minutes = Math.max(0, Math.round((now - timestamp) / 60_000));
  if (minutes < 1) return typeof value === "number" ? "live" : "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function buildMissionWorkQueue(
  input: BuildMissionWorkQueueInput,
): MissionWorkQueueModel {
  const now = input.now ?? Date.now();
  const byId = new Map<string, MissionWorkQueueRow>();
  for (const mission of input.summaries) {
    byId.set(mission.id, rowFromSummary(mission, now));
  }
  for (const mission of input.liveMissions) {
    const existing = byId.get(mission.id);
    byId.set(mission.id, rowFromLive(mission, existing, now));
  }

  const mergedRows = sortRows([...byId.values()]);
  const counts = {
    active: mergedRows.filter((row) => row.bucket !== "done").length,
    needsInput: mergedRows.filter((row) => row.bucket === "needs_input").length,
    done: mergedRows.filter((row) => row.bucket === "done").length,
    all: mergedRows.length,
  };
  const activeGoal = activeGoalFor(mergedRows, input.activeGoalMissionId ?? null);
  const rows = sortRows(
    mergedRows
      .filter((row) => filterMatches(row, input.filter))
      .filter((row) => queryMatches(row, input.query)),
  );
  return {
    rows,
    sections: sectionsFor(rows),
    counts,
    activeGoal,
  };
}

function rowFromSummary(
  mission: MissionSummary,
  now: number,
): MissionWorkQueueRow {
  const updatedAt = mission.last_event_at ?? mission.updated_at;
  const bucket = missionStatusBucket(mission.status);
  return {
    id: mission.id,
    title: mission.title,
    kind: mission.kind,
    status: mission.status,
    bucket,
    ...(mission.summary ? { summary: mission.summary } : {}),
    updatedAt,
    updatedAtMs: timestampFor(updatedAt),
    updatedLabel: formatMissionRelativeTime(updatedAt, now),
    usedTurns: mission.used_turns,
    budgetTurns: mission.budget_turns,
    action: missionActionForStatus(mission.status),
  };
}

function rowFromLive(
  mission: MissionActivity,
  existing: MissionWorkQueueRow | undefined,
  now: number,
): MissionWorkQueueRow {
  const bucket = missionStatusBucket(mission.status);
  return {
    id: mission.id,
    title: mission.title,
    kind: mission.kind,
    status: mission.status,
    bucket,
    ...(mission.detail ? { detail: mission.detail } : {}),
    ...(existing?.summary ? { summary: existing.summary } : {}),
    updatedAt: mission.updatedAt,
    updatedAtMs: timestampFor(mission.updatedAt),
    updatedLabel: formatMissionRelativeTime(mission.updatedAt, now),
    usedTurns: existing?.usedTurns ?? 0,
    budgetTurns: existing?.budgetTurns ?? null,
    action: missionActionForStatus(mission.status),
  };
}

function timestampFor(value: string | number): number {
  return typeof value === "number" ? value : Date.parse(value);
}

function sortRows(rows: MissionWorkQueueRow[]): MissionWorkQueueRow[] {
  return [...rows].sort((a, b) => {
    const rank = SECTION_RANK[a.bucket] - SECTION_RANK[b.bucket];
    if (rank !== 0) return rank;
    return (Number.isFinite(b.updatedAtMs) ? b.updatedAtMs : 0)
      - (Number.isFinite(a.updatedAtMs) ? a.updatedAtMs : 0);
  });
}

function filterMatches(
  row: MissionWorkQueueRow,
  filter: MissionWorkQueueFilter,
): boolean {
  if (filter === "all") return true;
  if (filter === "done") return row.bucket === "done";
  if (filter === "needs_input") return row.bucket === "needs_input";
  return row.bucket !== "done";
}

function queryMatches(row: MissionWorkQueueRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [
    row.title,
    row.kind,
    row.status,
    row.detail,
    row.summary,
  ]
    .filter((value): value is string => typeof value === "string")
    .some((value) => value.toLowerCase().includes(needle));
}

function sectionsFor(rows: MissionWorkQueueRow[]): MissionWorkQueueSection[] {
  const byKind = new Map<MissionWorkQueueSectionKind, MissionWorkQueueRow[]>();
  for (const row of rows) {
    const existing = byKind.get(row.bucket) ?? [];
    existing.push(row);
    byKind.set(row.bucket, existing);
  }
  return (["needs_input", "running", "done"] as const)
    .map((kind) => ({ kind, label: SECTION_LABELS[kind], rows: byKind.get(kind) ?? [] }))
    .filter((section) => section.rows.length > 0);
}

function activeGoalFor(
  rows: MissionWorkQueueRow[],
  activeGoalMissionId: string | null,
): MissionWorkQueueRow | null {
  const byId = activeGoalMissionId
    ? rows.find((row) => row.id === activeGoalMissionId && row.kind === "goal")
    : null;
  if (byId && byId.bucket !== "done") return byId;
  return rows.find((row) => row.kind === "goal" && row.bucket !== "done") ?? null;
}
