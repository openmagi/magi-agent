import type {
  ChannelState,
  ControlRequestRecord,
  QueuedMessage,
  SubagentActivity,
  TaskBoardTask,
  ToolActivity,
  ChatResponseLanguage,
} from "./types";
import { derivePublicToolPreview } from "./public-tool-preview";

export type WorkConsoleRowGroup =
  | "status"
  | "tool"
  | "subagent"
  | "task"
  | "queue"
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

function toolRow(activity: ToolActivity, language?: ChatResponseLanguage): WorkConsoleRow {
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
}: WorkConsoleInput): WorkConsoleRow[] {
  const rows: WorkConsoleRow[] = [];
  const language = channelState.responseLanguage;
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

  for (const [index, subagent] of (channelState.subagents ?? []).entries()) {
    rows.push({
      id: `subagent:${subagent.taskId}`,
      group: "subagent",
      label: subagentName(index),
      detail: subagent.detail,
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
