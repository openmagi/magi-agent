import type {
  ChannelState,
  ControlRequestRecord,
  MissionActivity,
  QueuedMessage,
  PatchPreviewFile,
  RuntimeTrace,
  SubagentActivity,
  TaskBoardTask,
  ToolActivity,
  ChatResponseLanguage,
} from "./types";
import { derivePublicToolPreview } from "./public-tool-preview";

export type WorkConsoleRowGroup =
  | "status"
  | "mission"
  | "tool"
  | "subagent"
  | "task"
  | "queue"
  | "trace"
  | "control";

export type WorkConsoleRowStatus =
  | "running"
  | "done"
  | "waiting"
  | "error"
  | "info";

export interface WorkConsoleRow {
  id: string;
  group: WorkConsoleRowGroup;
  label: string;
  detail?: string;
  snippet?: string;
  status: WorkConsoleRowStatus;
  meta?: string;
}

export interface WorkConsoleInput {
  channelState: ChannelState;
  queuedMessages?: QueuedMessage[];
  controlRequests?: ControlRequestRecord[];
  uiLanguage?: ChatResponseLanguage;
}

const SUBAGENT_NAMES = [
  "Halley",
  "Meitner",
  "Kant",
  "Noether",
  "Turing",
  "Curie",
  "Hopper",
  "Lovelace",
  "Feynman",
  "Franklin",
  "Shannon",
  "Lamarr",
];

const LOW_SIGNAL_TOOL_LABELS = new Set([
  "glob",
  "grep",
  "subagentrunning",
  "subagenttooldecision",
  "taskget",
  "taskread",
  "taskstatus",
]);

const PHASE_LABELS: Record<NonNullable<ChannelState["turnPhase"]>, string> = {
  pending: "Preparing",
  planning: "Planning",
  executing: "Running",
  verifying: "Verifying",
  committing: "Writing answer",
  committed: "Finalizing",
  aborted: "Interrupted",
};

const PHASE_LABELS_KO: Record<NonNullable<ChannelState["turnPhase"]>, string> = {
  pending: "준비 중",
  planning: "계획 중",
  executing: "실행 중",
  verifying: "검증 중",
  committing: "답변 작성 중",
  committed: "마무리 중",
  aborted: "중단됨",
};

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

function formatElapsed(ms?: number | null, language?: ChatResponseLanguage): string | undefined {
  if (!ms || ms < 1000) return undefined;
  const seconds = Math.max(1, Math.round(ms / 1000));
  return isKorean(language) ? `${seconds}초` : `${seconds}s`;
}

function pendingControlRequests(
  requests?: ControlRequestRecord[],
): ControlRequestRecord[] {
  return (requests ?? []).filter((request) => request.state === "pending");
}

function statusFromTool(activity: ToolActivity): WorkConsoleRowStatus {
  switch (activity.status) {
    case "running":
      return "running";
    case "done":
      return "done";
    case "error":
    case "denied":
      return "error";
    default:
      return "info";
  }
}

function statusFromSubagent(activity: SubagentActivity): WorkConsoleRowStatus {
  switch (activity.status) {
    case "running":
      return "running";
    case "waiting":
      return "waiting";
    case "done":
      return "done";
    case "error":
    case "cancelled":
      return "error";
    default:
      return "info";
  }
}

function statusFromMission(mission: MissionActivity): WorkConsoleRowStatus {
  switch (mission.status) {
    case "running":
      return "running";
    case "completed":
      return "done";
    case "failed":
    case "cancelled":
      return "error";
    case "queued":
    case "blocked":
    case "waiting":
    case "paused":
    default:
      return "waiting";
  }
}

function statusFromRuntimeTrace(trace: RuntimeTrace): WorkConsoleRowStatus {
  if (trace.severity === "error") return "error";
  if (trace.severity === "warning") return "waiting";
  return "info";
}

function statusFromTask(task: TaskBoardTask): WorkConsoleRowStatus {
  switch (task.status) {
    case "in_progress":
      return "running";
    case "completed":
      return "done";
    case "cancelled":
      return "error";
    case "pending":
    default:
      return "waiting";
  }
}

function taskMeta(task: TaskBoardTask, language?: ChatResponseLanguage): string {
  switch (task.status) {
    case "in_progress":
      return t(language, "running", "진행 중");
    case "completed":
      return t(language, "done", "완료");
    case "cancelled":
      return t(language, "cancelled", "취소됨");
    case "pending":
    default:
      return t(language, "pending", "대기 중");
  }
}

function missionMeta(mission: MissionActivity): string {
  return `${mission.kind} ${mission.status}`;
}

function runtimeTraceLabel(
  trace: RuntimeTrace,
  language?: ChatResponseLanguage,
): string {
  switch (trace.phase) {
    case "retry_scheduled":
      return t(language, "Retry scheduled", "재시도 예정");
    case "retry_aborted":
      return t(language, "Retry stopped", "재시도 중단");
    case "terminal_abort":
      return t(language, "Turn stopped", "턴 중단");
    case "verifier_blocked":
    default:
      return t(language, "Runtime verifier blocked completion", "런타임 검증에서 완료 차단");
  }
}

function runtimeTraceMeta(trace: RuntimeTrace): string | undefined {
  const attempt =
    typeof trace.attempt === "number" && typeof trace.maxAttempts === "number"
      ? `${trace.attempt}/${trace.maxAttempts}`
      : undefined;
  return [trace.reasonCode, attempt].filter(Boolean).join(" · ") || undefined;
}

function runtimeTraceRow(
  trace: RuntimeTrace,
  language?: ChatResponseLanguage,
): WorkConsoleRow {
  return {
    id: `trace:${trace.turnId}:${trace.receivedAt}:${trace.reasonCode ?? trace.phase}`,
    group: "trace",
    label: runtimeTraceLabel(trace, language),
    detail: trace.requiredAction ?? trace.detail ?? trace.title,
    status: statusFromRuntimeTrace(trace),
    ...(trace.detail && trace.requiredAction ? { snippet: trace.detail } : {}),
    ...(runtimeTraceMeta(trace) ? { meta: runtimeTraceMeta(trace) } : {}),
  };
}

function subagentName(index: number): string {
  return SUBAGENT_NAMES[index % SUBAGENT_NAMES.length] ?? `Agent ${index + 1}`;
}

function normalizeRole(role: string): string {
  const value = role.trim().toLowerCase();
  if (value === "explore" || value === "explorer" || value === "research") return "explorer";
  if (value === "review" || value === "reviewer") return "reviewer";
  if (value === "work" || value === "worker") return "worker";
  return value || "subagent";
}

function subagentDetail(
  activity: SubagentActivity,
  language?: ChatResponseLanguage,
): string | undefined {
  const detail = activity.detail?.replace(/\s+/g, " ").trim();
  if (!detail) return undefined;

  const normalized = detail.toLowerCase();
  if (/^iteration\s+\d+$/.test(normalized)) return undefined;
  if (normalized === "allow" || normalized === "allowed" || normalized === "permission") {
    return activity.status === "waiting"
      ? t(language, "Checking permissions", "권한 확인 중")
      : t(language, "Permission checked", "권한 확인됨");
  }

  return detail;
}

function controlLabel(
  request: ControlRequestRecord,
  language?: ChatResponseLanguage,
): string {
  if (request.kind === "user_question") return t(language, "Needs answer", "답변 필요");
  return t(language, "Needs approval", "승인 필요");
}

function controlMeta(
  request: ControlRequestRecord,
  language?: ChatResponseLanguage,
): string {
  switch (request.kind) {
    case "plan_approval":
      return t(language, "plan", "계획");
    case "user_question":
      return t(language, "question", "질문");
    case "tool_permission":
    default:
      return t(language, "tool", "도구");
  }
}

function normalizeToolLabel(label: string): string {
  return label.replace(/[^a-z0-9]/gi, "").toLowerCase();
}

function shouldDisplayToolActivity(activity: ToolActivity): boolean {
  return !LOW_SIGNAL_TOOL_LABELS.has(normalizeToolLabel(activity.label));
}

function patchOperationLabel(
  file: PatchPreviewFile,
  language?: ChatResponseLanguage,
): string {
  if (isKorean(language)) {
    if (file.operation === "create") return "생성";
    if (file.operation === "delete") return "삭제";
    return "수정";
  }
  if (file.operation === "create") return "Create";
  if (file.operation === "delete") return "Delete";
  return "Update";
}

function patchAction(activity: ToolActivity, language?: ChatResponseLanguage): string {
  const preview = activity.patchPreview;
  if (preview?.dryRun) return t(language, "Previewing patch", "패치 미리보기");
  if (activity.status === "done") return t(language, "Applied patch", "패치 적용됨");
  if (activity.status === "error" || activity.status === "denied") {
    return t(language, "Patch blocked", "패치 차단됨");
  }
  return t(language, "Reviewing patch", "패치 검토 중");
}

function patchTarget(files: string[], language?: ChatResponseLanguage): string | undefined {
  if (files.length === 0) return undefined;
  const visible = files.slice(0, 3).join(", ");
  const suffix = files.length > 3 ? `, +${files.length - 3}` : "";
  const noun = files.length === 1
    ? t(language, "file", "파일")
    : t(language, "files", "파일");
  return `${files.length} ${noun}: ${visible}${suffix}`;
}

function patchSnippet(
  files: PatchPreviewFile[],
  language?: ChatResponseLanguage,
): string | undefined {
  if (files.length === 0) return undefined;
  const lines = files.slice(0, 4).map((file) =>
    `${patchOperationLabel(file, language)} ${file.path} (+${file.addedLines}/-${file.removedLines})`
  );
  if (files.length > 4) {
    lines.push(t(language, `+${files.length - 4} more files`, `외 ${files.length - 4}개 파일`));
  }
  return lines.join("\n");
}

function patchPreviewRow(
  activity: ToolActivity,
  language?: ChatResponseLanguage,
): WorkConsoleRow | null {
  const preview = activity.patchPreview;
  if (!preview) return null;
  const duration = activity.durationMs ? formatElapsed(activity.durationMs, language) : undefined;
  return {
    id: `tool:${activity.id}`,
    group: "tool",
    label: patchAction(activity, language),
    detail: patchTarget(preview.changedFiles, language),
    snippet: patchSnippet(preview.files, language),
    status: statusFromTool(activity),
    ...(duration ? { meta: duration } : {}),
  };
}

function toolRow(activity: ToolActivity, language?: ChatResponseLanguage): WorkConsoleRow {
  const patchRow = patchPreviewRow(activity, language);
  if (patchRow) return patchRow;

  const preview = derivePublicToolPreview({
    label: activity.label,
    inputPreview: activity.inputPreview,
    outputPreview: activity.outputPreview,
    language,
  });
  const duration = activity.durationMs ? formatElapsed(activity.durationMs, language) : undefined;

  return {
    id: `tool:${activity.id}`,
    group: "tool",
    label: preview?.action ?? activity.label,
    detail: preview?.target,
    snippet: preview?.snippet,
    status: statusFromTool(activity),
    ...(duration ? { meta: duration } : {}),
  };
}

export function deriveWorkConsoleRows({
  channelState,
  queuedMessages = [],
  controlRequests = [],
  uiLanguage,
}: WorkConsoleInput): WorkConsoleRow[] {
  const rows: WorkConsoleRow[] = [];
  const language = uiLanguage ?? channelState.responseLanguage;
  const phase = channelState.reconnecting
    ? t(language, "Reconnecting", "다시 연결 중")
    : channelState.error
      ? t(language, "Blocked", "차단됨")
      : channelState.turnPhase
        ? (isKorean(language)
          ? PHASE_LABELS_KO[channelState.turnPhase]
          : PHASE_LABELS[channelState.turnPhase])
        : channelState.streaming
          ? t(language, "Working", "작업 중")
          : null;
  const elapsed = formatElapsed(channelState.heartbeatElapsedMs, language);

  if (phase) {
    rows.push({
      id: "phase",
      group: "status",
      label: phase,
      detail: elapsed ? t(language, `${elapsed} elapsed`, `${elapsed} 경과`) : undefined,
      status: channelState.error || channelState.turnPhase === "aborted" ? "error" : "running",
    });
  }

  for (const mission of channelState.missions ?? []) {
    rows.push({
      id: `mission:${mission.id}`,
      group: "mission",
      label: mission.title,
      detail: mission.detail,
      status: statusFromMission(mission),
      meta: missionMeta(mission),
    });
  }

  for (const trace of (channelState.runtimeTraces ?? []).slice(-6)) {
    rows.push(runtimeTraceRow(trace, language));
  }

  for (const [index, subagent] of (channelState.subagents ?? []).entries()) {
    rows.push({
      id: `subagent:${subagent.taskId}`,
      group: "subagent",
      label: subagentName(index),
      detail: subagentDetail(subagent, language),
      status: statusFromSubagent(subagent),
      meta: normalizeRole(subagent.role),
    });
  }

  for (const activity of channelState.activeTools ?? []) {
    if (!shouldDisplayToolActivity(activity)) continue;
    rows.push(toolRow(activity, language));
  }

  for (const task of channelState.taskBoard?.tasks ?? []) {
    rows.push({
      id: `task:${task.id}`,
      group: "task",
      label: task.title,
      detail: task.description,
      status: statusFromTask(task),
      meta: taskMeta(task, language),
    });
  }

  for (const [index, message] of queuedMessages.entries()) {
    rows.push({
      id: `queue:${message.id}`,
      group: "queue",
      label: index === 0
        ? t(language, "Queued follow-up", "대기 중인 후속 메시지")
        : t(language, `Queued follow-up ${index + 1}`, `대기 중인 후속 메시지 ${index + 1}`),
      detail: message.content,
      status: message.priority === "now" ? "running" : "waiting",
      meta: message.priority === "now"
        ? t(language, "steering next", "다음 턴 조정")
        : t(language, "will send later", "나중에 전송"),
    });
  }

  for (const request of pendingControlRequests(controlRequests)) {
    rows.push({
      id: `control:${request.requestId}`,
      group: "control",
      label: controlLabel(request, language),
      detail: request.prompt,
      status: "waiting",
      meta: controlMeta(request, language),
    });
  }

  if (rows.length === 0) {
    return [
      {
        id: "idle",
        group: "status",
        label: t(language, "Idle", "대기 중"),
        detail: t(language, "Live agent work will appear here.", "실시간 작업 상태가 여기에 표시됩니다."),
        status: "info",
      },
    ];
  }

  return rows;
}
