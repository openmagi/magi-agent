import type {
  MissionArtifact,
  MissionDetail,
  MissionEvent,
  MissionRun,
} from "./types";

export type MissionTimelineKind =
  | "created"
  | "started"
  | "heartbeat"
  | "child_spawned"
  | "evidence_attached"
  | "blocked"
  | "resumed"
  | "cancelled"
  | "failed"
  | "completed"
  | "comment"
  | "retry_requested"
  | "unblocked"
  | "delivered"
  | "artifact";

export type MissionTimelineActor =
  | "user"
  | "parent_agent"
  | "child_agent"
  | "cron"
  | "runtime";

export type MissionEvidenceKind = MissionArtifact["kind"] | "run_preview";

export type MissionTimelineFilter = "all" | "user" | "runtime" | "evidence";
export type MissionTimelineFilterCategory = Exclude<MissionTimelineFilter, "all">;

const EVIDENCE_RANK: Record<MissionEvidenceKind, number> = {
  subagent_output: 0,
  artifact: 1,
  file: 2,
  browser_screenshot: 3,
  url: 4,
  kb_document: 5,
  stdout: 6,
  run_preview: 7,
};

export interface MissionTimelineRun {
  id: string;
  shortRunId: string;
  attempt: number;
  triggerType: MissionRun["trigger_type"];
  status: MissionRun["status"];
  label: string;
  startedAt: string;
  finishedAt: string | null;
  sessionKey: string | null;
  turnId: string | null;
  spawnTaskId: string | null;
  cronId: string | null;
  detail?: string;
}

export interface MissionTimelineItem {
  id: string;
  kind: MissionTimelineKind;
  label: string;
  createdAt: string;
  actorType: MissionTimelineActor;
  actorId: string | null;
  attempt: number | null;
  runId: string | null;
  shortRunId: string | null;
  runLabel: string | null;
  filterCategory: MissionTimelineFilterCategory;
  message?: string;
  detail?: string;
}

export interface MissionHandoffEvidence {
  id: string;
  kind: MissionEvidenceKind;
  title: string;
  createdAt: string;
  attempt: number | null;
  runId: string | null;
  shortRunId: string | null;
  preview?: string;
  uri?: string;
  storageKey?: string;
  sourceLabel: string;
  persona?: string;
  taskId?: string;
  sourceId?: string;
  parallelGroup?: string;
  synthesisId?: string;
}

export interface MissionTimelineAttemptGroup {
  id: string;
  label: string;
  attempt: number | null;
  runId: string | null;
  shortRunId: string | null;
  triggerType: MissionRun["trigger_type"] | null;
  status: MissionRun["status"] | null;
  items: MissionTimelineItem[];
  evidence: MissionHandoffEvidence[];
  itemCount: number;
  evidenceCount: number;
}

export interface MissionTimeline {
  runs: MissionTimelineRun[];
  runsById: Map<string, MissionTimelineRun>;
  items: MissionTimelineItem[];
  evidence: MissionHandoffEvidence[];
  attemptGroups: MissionTimelineAttemptGroup[];
  filterCounts: Record<MissionTimelineFilter, number>;
}

const KIND_RANK: Record<MissionTimelineKind, number> = {
  created: 0,
  started: 1,
  child_spawned: 2,
  heartbeat: 3,
  evidence_attached: 4,
  artifact: 5,
  retry_requested: 6,
  resumed: 7,
  unblocked: 8,
  blocked: 9,
  delivered: 10,
  comment: 11,
  cancelled: 12,
  failed: 13,
  completed: 14,
};

const EVENT_KIND: Record<MissionEvent["event_type"], MissionTimelineKind> = {
  created: "created",
  claimed: "started",
  heartbeat: "heartbeat",
  evidence: "evidence_attached",
  comment: "comment",
  blocked: "blocked",
  unblocked: "unblocked",
  retry_requested: "retry_requested",
  cancel_requested: "cancelled",
  cancelled: "cancelled",
  completed: "completed",
  failed: "failed",
  delivered: "delivered",
  paused: "blocked",
  resumed: "resumed",
};

const KIND_LABEL: Record<MissionTimelineKind, string> = {
  created: "created",
  started: "started",
  heartbeat: "heartbeat",
  child_spawned: "child spawned",
  evidence_attached: "evidence attached",
  blocked: "blocked",
  resumed: "resumed",
  cancelled: "cancelled",
  failed: "failed",
  completed: "completed",
  comment: "comment",
  retry_requested: "retry requested",
  unblocked: "unblocked",
  delivered: "delivered",
  artifact: "artifact",
};

export function buildMissionTimeline(detail: MissionDetail): MissionTimeline {
  const runs = buildRunEntries(detail.runs);
  const runsById = new Map(runs.map((run) => [run.id, run]));
  const items = [
    ...syntheticRunItems(runs),
    ...eventItems(detail.events, runsById),
    ...artifactItems(detail.artifacts, runsById),
    ...syntheticMissionCreatedItems(detail, runsById),
  ].sort(compareTimelineItems);
  const evidence = buildEvidence(detail, runsById);
  return {
    runs,
    runsById,
    items,
    evidence,
    attemptGroups: buildAttemptGroups(runs, items, evidence),
    filterCounts: buildFilterCounts(items),
  };
}

export function filterMissionTimelineItems(
  timeline: Pick<MissionTimeline, "items">,
  filter: MissionTimelineFilter,
): MissionTimelineItem[] {
  if (filter === "all") return timeline.items;
  return timeline.items.filter((item) => item.filterCategory === filter);
}

function buildRunEntries(runs: MissionRun[]): MissionTimelineRun[] {
  return [...runs]
    .sort((left, right) => compareDateStrings(left.started_at, right.started_at, left.id, right.id))
    .map((run, index) => {
      const attempt = index + 1;
      const detail = run.error_message ?? run.result_preview ?? run.stdout_preview ?? undefined;
      return {
        id: run.id,
        shortRunId: shortId(run.id),
        attempt,
        triggerType: run.trigger_type,
        status: run.status,
        label: `Attempt ${attempt} · ${run.trigger_type} ${run.status}`,
        startedAt: run.started_at,
        finishedAt: run.finished_at,
        sessionKey: run.session_key,
        turnId: run.turn_id,
        spawnTaskId: run.spawn_task_id,
        cronId: run.cron_id,
        ...(detail ? { detail: safeText(detail, 500) } : {}),
      };
    });
}

function syntheticRunItems(runs: MissionTimelineRun[]): MissionTimelineItem[] {
  return runs.flatMap((run) => {
    const started: MissionTimelineItem = {
      id: `run:${run.id}:started`,
      kind: "started",
      label: KIND_LABEL.started,
      createdAt: run.startedAt,
      actorType: actorForRunStart(run),
      actorId: null,
      attempt: run.attempt,
      runId: run.id,
      shortRunId: run.shortRunId,
      runLabel: run.label,
      filterCategory: "runtime",
      detail: run.detail ?? run.triggerType,
    };
    const items = [started];

    if (run.triggerType === "handoff" && run.spawnTaskId) {
      items.push({
        id: `run:${run.id}:child-spawned`,
        kind: "child_spawned",
        label: KIND_LABEL.child_spawned,
        createdAt: run.startedAt,
        actorType: "parent_agent",
        actorId: null,
        attempt: run.attempt,
        runId: run.id,
        shortRunId: run.shortRunId,
        runLabel: run.label,
        filterCategory: "evidence",
        detail: `SpawnAgent task ${run.spawnTaskId}`,
      });
    }

    if (run.finishedAt) {
      const terminalKind = terminalKindForRun(run.status);
      items.push({
        id: `run:${run.id}:terminal`,
        kind: terminalKind,
        label: KIND_LABEL[terminalKind],
        createdAt: run.finishedAt,
        actorType: actorForRunStart(run),
        actorId: null,
        attempt: run.attempt,
        runId: run.id,
        shortRunId: run.shortRunId,
        runLabel: run.label,
        filterCategory: "runtime",
        ...(run.detail ? { detail: run.detail } : {}),
      });
    }

    return items;
  });
}

function eventItems(
  events: MissionEvent[],
  runsById: Map<string, MissionTimelineRun>,
): MissionTimelineItem[] {
  return events.map((event) => {
    const payload = objectOrEmpty(event.payload);
    const category = payloadString(payload, "category");
    const kind =
      event.event_type === "evidence" && category === "child_spawned"
        ? "child_spawned"
        : EVENT_KIND[event.event_type];
    const run = event.run_id ? runsById.get(event.run_id) ?? null : null;
    const detail = eventDetail(event, payload);
    return {
      id: event.id,
      kind,
      label: eventTimelineLabel(event, payload, kind),
      createdAt: event.created_at,
      actorType: actorForEvent(event, payload),
      actorId: event.actor_id,
      attempt: run?.attempt ?? null,
      runId: event.run_id,
      shortRunId: run?.shortRunId ?? null,
      runLabel: run?.label ?? null,
      filterCategory: filterCategoryForItem(kind, actorForEvent(event, payload)),
      ...(event.message ? { message: safeText(event.message, 500) } : {}),
      ...(detail ? { detail } : {}),
    };
  });
}

function artifactItems(
  artifacts: MissionArtifact[],
  runsById: Map<string, MissionTimelineRun>,
): MissionTimelineItem[] {
  return artifacts.map((artifact) => {
    const run = artifact.run_id ? runsById.get(artifact.run_id) ?? null : null;
    const preview = artifact.preview ? safeText(artifact.preview, 500) : null;
    return {
      id: `artifact:${artifact.id}`,
      kind: "evidence_attached",
      label: KIND_LABEL.evidence_attached,
      createdAt: artifact.created_at,
      actorType: artifact.kind === "subagent_output" ? "child_agent" : "runtime",
      actorId: null,
      attempt: run?.attempt ?? null,
      runId: artifact.run_id,
      shortRunId: run?.shortRunId ?? null,
      runLabel: run?.label ?? null,
      filterCategory: "evidence",
      message: artifact.title,
      ...(preview ? { detail: preview } : {}),
    };
  });
}

function syntheticMissionCreatedItems(
  detail: MissionDetail,
  runsById: Map<string, MissionTimelineRun>,
): MissionTimelineItem[] {
  if (detail.events.some((event) => event.event_type === "created")) return [];
  const run = detail.runs.find((candidate) => candidate.trigger_type === "user");
  const runEntry = run ? runsById.get(run.id) ?? null : null;
  return [
    {
      id: `mission:${detail.mission.id}:created`,
      kind: "created",
      label: KIND_LABEL.created,
      createdAt: detail.mission.created_at,
      actorType: createdByActor(detail.mission.created_by),
      actorId: null,
      attempt: runEntry?.attempt ?? null,
      runId: runEntry?.id ?? null,
      shortRunId: runEntry?.shortRunId ?? null,
      runLabel: runEntry?.label ?? null,
      filterCategory: filterCategoryForItem("created", createdByActor(detail.mission.created_by)),
      message: detail.mission.title,
      ...(detail.mission.summary ? { detail: safeText(detail.mission.summary, 500) } : {}),
    },
  ];
}

function buildAttemptGroups(
  runs: MissionTimelineRun[],
  items: MissionTimelineItem[],
  evidence: MissionHandoffEvidence[],
): MissionTimelineAttemptGroup[] {
  const groups: MissionTimelineAttemptGroup[] = [];
  const missionItems = items.filter((item) => !item.runId);
  const missionEvidence = evidence.filter((item) => !item.runId);
  if (missionItems.length > 0 || missionEvidence.length > 0) {
    groups.push({
      id: "mission",
      label: "Mission events",
      attempt: null,
      runId: null,
      shortRunId: null,
      triggerType: null,
      status: null,
      items: missionItems,
      evidence: missionEvidence,
      itemCount: missionItems.length,
      evidenceCount: missionEvidence.length,
    });
  }

  for (const run of runs) {
    const runItems = items.filter((item) => item.runId === run.id);
    const runEvidence = evidence.filter((item) => item.runId === run.id);
    groups.push({
      id: run.id,
      label: run.label,
      attempt: run.attempt,
      runId: run.id,
      shortRunId: run.shortRunId,
      triggerType: run.triggerType,
      status: run.status,
      items: runItems,
      evidence: runEvidence,
      itemCount: runItems.length,
      evidenceCount: runEvidence.length,
    });
  }

  return groups.filter((group) => group.itemCount > 0 || group.evidenceCount > 0);
}

function buildFilterCounts(
  items: MissionTimelineItem[],
): Record<MissionTimelineFilter, number> {
  return {
    all: items.length,
    user: items.filter((item) => item.filterCategory === "user").length,
    runtime: items.filter((item) => item.filterCategory === "runtime").length,
    evidence: items.filter((item) => item.filterCategory === "evidence").length,
  };
}

function buildEvidence(
  detail: MissionDetail,
  runsById: Map<string, MissionTimelineRun>,
): MissionHandoffEvidence[] {
  const artifactEvidence = detail.artifacts.map((artifact) => {
    const run = artifact.run_id ? runsById.get(artifact.run_id) ?? null : null;
    const metadata = objectOrEmpty(artifact.metadata);
    return {
      id: artifact.id,
      kind: artifact.kind,
      title: artifact.title,
      createdAt: artifact.created_at,
      attempt: run?.attempt ?? null,
      runId: artifact.run_id,
      shortRunId: run?.shortRunId ?? null,
      sourceLabel: evidenceSourceLabel(artifact.kind, metadata),
      ...(artifact.preview ? { preview: safeText(artifact.preview, 500) } : {}),
      ...(artifact.uri ? { uri: artifact.uri } : {}),
      ...(artifact.storage_key ? { storageKey: artifact.storage_key } : {}),
      ...(payloadString(metadata, "persona") ? { persona: payloadString(metadata, "persona") } : {}),
      ...(payloadString(metadata, "taskId") ? { taskId: payloadString(metadata, "taskId") } : {}),
      ...(payloadString(metadata, "sourceId") ? { sourceId: payloadString(metadata, "sourceId") } : {}),
      ...(payloadString(metadata, "parallelGroup") ? { parallelGroup: payloadString(metadata, "parallelGroup") } : {}),
      ...(payloadString(metadata, "synthesisId") ? { synthesisId: payloadString(metadata, "synthesisId") } : {}),
    } satisfies MissionHandoffEvidence;
  });

  const runEvidence = detail.runs
    .filter((run) => run.result_preview || run.stdout_preview || run.error_message)
    .map((run) => {
      const entry = runsById.get(run.id) ?? null;
      const preview = run.error_message ?? run.result_preview ?? run.stdout_preview ?? "";
      return {
        id: `run:${run.id}:preview`,
        kind: "run_preview" as const,
        title: `${run.trigger_type} ${run.status}`,
        createdAt: run.finished_at ?? run.started_at,
        attempt: entry?.attempt ?? null,
        runId: run.id,
        shortRunId: entry?.shortRunId ?? shortId(run.id),
        sourceLabel: "Run preview",
        preview: safeText(preview, 500),
      } satisfies MissionHandoffEvidence;
    });

  return [...artifactEvidence, ...runEvidence].sort(compareEvidence);
}

function compareEvidence(
  left: MissionHandoffEvidence,
  right: MissionHandoffEvidence,
): number {
  const byKind = EVIDENCE_RANK[left.kind] - EVIDENCE_RANK[right.kind];
  if (byKind !== 0) return byKind;
  return compareDateStrings(left.createdAt, right.createdAt, left.id, right.id);
}

function actorForRunStart(run: MissionTimelineRun): MissionTimelineActor {
  if (run.triggerType === "cron" || run.triggerType === "script_cron") return "cron";
  return "runtime";
}

function filterCategoryForItem(
  kind: MissionTimelineKind,
  actorType: MissionTimelineActor,
): MissionTimelineFilterCategory {
  if (kind === "child_spawned" || kind === "evidence_attached" || kind === "artifact") {
    return "evidence";
  }
  return actorType === "user" ? "user" : "runtime";
}

function actorForEvent(
  event: MissionEvent,
  payload: Record<string, unknown>,
): MissionTimelineActor {
  if (event.actor_type === "user") return "user";
  if (event.actor_type === "cron") return "cron";
  if (event.actor_type === "system") return "runtime";
  const category = payloadString(payload, "category");
  return category === "child_result" ? "child_agent" : "parent_agent";
}

function createdByActor(createdBy: string): MissionTimelineActor {
  if (createdBy === "user") return "user";
  if (createdBy === "cron") return "cron";
  if (createdBy === "agent") return "parent_agent";
  return "runtime";
}

function terminalKindForRun(status: MissionRun["status"]): MissionTimelineKind {
  if (status === "completed") return "completed";
  if (status === "cancelled") return "cancelled";
  return "failed";
}

function eventDetail(
  event: MissionEvent,
  payload: Record<string, unknown>,
): string | undefined {
  const parts = actionDetailParts(event, payload);
  parts.push(...[
    payloadString(payload, "category"),
    payloadString(payload, "quietReason"),
    payloadString(payload, "persona"),
    payloadString(payload, "status"),
    payloadString(payload, "taskId"),
  ]
    .filter((value): value is string => Boolean(value))
    .map(humanizeToken));
  return parts.length > 0 ? unique(parts).join(" · ") : undefined;
}

function evidenceSourceLabel(
  kind: MissionArtifact["kind"],
  metadata: Record<string, unknown>,
): string {
  const category = payloadString(metadata, "category");
  if (category === "parallel_synthesis") return "Parallel synthesis";
  if (category === "parallel_research_evidence") return "Parallel research evidence";
  if (kind === "subagent_output") return "Child-agent evidence";
  if (kind === "browser_screenshot") return "Browser screenshot";
  if (kind === "kb_document") return "Knowledge document";
  if (kind === "stdout") return "Stdout";
  if (kind === "url") return "URL evidence";
  if (kind === "file") return "File evidence";
  return "Artifact evidence";
}

function eventTimelineLabel(
  event: MissionEvent,
  payload: Record<string, unknown>,
  kind: MissionTimelineKind,
): string {
  const reason = payloadString(payload, "reason");
  const sourceEventType = payloadString(payload, "sourceEventType");
  if (event.event_type === "retry_requested") {
    if (reason === "restart_recovery") return "Restart recovery requested";
    return event.actor_type === "user" ? "Retry requested by user" : "Retry requested";
  }
  if (event.event_type === "unblocked") {
    return event.actor_type === "user" ? "Unblocked by user" : "Unblocked";
  }
  if (event.event_type === "cancel_requested") {
    return event.actor_type === "user" ? "Cancel requested by user" : "Cancel requested";
  }
  if (event.event_type === "cancelled") {
    if (event.actor_type === "user" || reason === "mission_cancel_requested") {
      return "Cancelled by user";
    }
    return KIND_LABEL[kind];
  }
  if (event.event_type === "resumed") {
    if (reason === "restart_recovery") return "Resumed after restart";
    if (sourceEventType === "retry_requested") return "Retry run started";
    if (sourceEventType === "unblocked") return "Resumed after unblock";
    return event.actor_type === "user" ? "Resumed by user" : "Resumed";
  }
  if (event.event_type === "blocked" || event.event_type === "paused") return "Needs input";
  return KIND_LABEL[kind];
}

function actionDetailParts(
  event: MissionEvent,
  payload: Record<string, unknown>,
): string[] {
  const reason = payloadString(payload, "reason");
  const sourceEventType = payloadString(payload, "sourceEventType");
  const parts: string[] = [];
  if (event.event_type === "cancel_requested" && event.actor_type === "user") {
    parts.push("user cancellation");
  } else if (event.event_type === "cancelled" && reason === "mission_cancel_requested") {
    parts.push("user cancellation");
  } else if (event.event_type === "resumed" && reason === "restart_recovery") {
    parts.push("restart recovery");
  } else if (event.event_type === "resumed" && sourceEventType === "retry_requested") {
    parts.push("from user retry");
  } else if (event.event_type === "resumed" && sourceEventType === "unblocked") {
    parts.push("from user unblock");
  }
  return parts;
}

function payloadString(
  payload: Record<string, unknown>,
  key: string,
): string | undefined {
  const value = payload[key];
  if (typeof value !== "string") return undefined;
  const safe = safeText(value, 160);
  return safe.length > 0 ? safe : undefined;
}

function objectOrEmpty(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function safeText(value: string, limit: number): string {
  const trimmed = value.trim();
  return trimmed.length <= limit ? trimmed : trimmed.slice(0, limit).trimEnd();
}

function humanizeToken(value: string): string {
  return value.replace(/[_-]+/g, " ");
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

function shortId(id: string): string {
  return id.length <= 8 ? id : id.slice(0, 8);
}

function compareTimelineItems(left: MissionTimelineItem, right: MissionTimelineItem): number {
  const byDate = compareDateStrings(left.createdAt, right.createdAt, left.id, right.id);
  if (byDate !== 0) return byDate;
  const byKind = KIND_RANK[left.kind] - KIND_RANK[right.kind];
  if (byKind !== 0) return byKind;
  return left.id.localeCompare(right.id);
}

function compareDateStrings(
  leftDate: string,
  rightDate: string,
  leftFallback: string,
  rightFallback: string,
): number {
  const left = Date.parse(leftDate);
  const right = Date.parse(rightDate);
  const safeLeft = Number.isFinite(left) ? left : 0;
  const safeRight = Number.isFinite(right) ? right : 0;
  if (safeLeft !== safeRight) return safeLeft - safeRight;
  return leftFallback.localeCompare(rightFallback);
}
