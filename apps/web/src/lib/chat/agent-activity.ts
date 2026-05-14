import type { ChatResponseLanguage, TaskBoardSnapshot, ToolActivity } from "./types";

export type AgentActivityStatus = "running" | "done" | "error" | "denied";

export interface AgentActivityItem {
  id: string;
  label: string;
  status: AgentActivityStatus;
  detail?: string;
  actionCount?: number;
  durationMs?: number;
  inputPreview?: string;
  outputPreview?: string;
}

export interface DeriveAgentActivityInput {
  live?: boolean;
  startedAt?: number | null;
  thinkingContent?: string;
  thinkingDuration?: number;
  now?: number;
  fileProcessing?: boolean;
  turnPhase?: "pending" | "planning" | "executing" | "verifying" | "committing" | "compacting" | "committed" | "aborted" | null;
  heartbeatElapsedMs?: number | null;
  pendingInjectionCount?: number;
  activities?: ToolActivity[];
  taskBoard?: TaskBoardSnapshot | null;
  responseLanguage?: ChatResponseLanguage;
}

type ActivityCategory = "command" | "read" | "knowledge" | "other";

function plural(count: number, singular: string, pluralText: string): string {
  return count === 1 ? singular : pluralText;
}

function secondsBetween(startedAt: number, now: number): number {
  return Math.max(0, Math.round((now - startedAt) / 1000));
}

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

function thinkingDurationLabel(
  seconds: number,
  language: ChatResponseLanguage | undefined,
  completed: boolean,
): string {
  if (language === "ko") return `${seconds}초 동안 작업`;
  if (language === undefined) return `${seconds}s 동안 작업`;
  return completed ? `Worked for ${seconds}s` : `Working for ${seconds}s`;
}

function formatSeconds(seconds: number, language?: ChatResponseLanguage): string {
  const rounded = Math.max(0, Math.round(seconds));
  return isKorean(language) ? `${rounded}초` : `${rounded}s`;
}

function describePhase(
  phase: NonNullable<DeriveAgentActivityInput["turnPhase"]>,
  language?: ChatResponseLanguage,
  elapsedSeconds?: number,
): Pick<AgentActivityItem, "label" | "detail"> | null {
  switch (phase) {
    case "pending":
      return { label: t(language, "Preparing turn", "턴 준비 중") };
    case "planning":
      return {
        label: t(language, "Planning next steps", "다음 단계 계획 중"),
        ...(typeof elapsedSeconds === "number" ? { detail: formatSeconds(elapsedSeconds, language) } : {}),
      };
    case "executing":
      return {
        label: t(language, "Running current step", "현재 단계 실행 중"),
        ...(typeof elapsedSeconds === "number" ? { detail: formatSeconds(elapsedSeconds, language) } : {}),
      };
    case "verifying":
      return {
        label: t(language, "Verifying results", "결과 검증 중"),
        ...(typeof elapsedSeconds === "number" ? { detail: formatSeconds(elapsedSeconds, language) } : {}),
      };
    case "committing":
      return {
        label: t(language, "Preparing final answer", "최종 답변 준비 중"),
        ...(typeof elapsedSeconds === "number" ? { detail: formatSeconds(elapsedSeconds, language) } : {}),
      };
    case "compacting":
      return {
        label: t(language, "Compacting memory", "메모리 압축 중"),
        ...(typeof elapsedSeconds === "number" ? { detail: formatSeconds(elapsedSeconds, language) } : {}),
      };
    case "aborted":
      return { label: t(language, "Stopping current turn", "현재 턴 중단 중") };
    case "committed":
      return { label: t(language, "Finalizing response", "답변 마무리 중") };
    default:
      return null;
  }
}

function categorizeActivity(label: string): ActivityCategory {
  const normalized = label.toLowerCase();
  if (
    /\b(rg|grep|sed|cat|ls|find)\b/.test(normalized) ||
    normalized.includes("fetch_file") ||
    normalized.includes("read")
  ) {
    return "read";
  }
  if (
    normalized.includes("exec") ||
    normalized.includes("command") ||
    normalized.includes("shell") ||
    /\b(npm|git|kubectl|vercel|bash|zsh)\b/.test(normalized)
  ) {
    return "command";
  }
  if (
    normalized.includes("kb") ||
    normalized.includes("knowledge") ||
    normalized.includes("document")
  ) {
    return "knowledge";
  }
  return "other";
}

function summarizeTaskBoard(
  taskBoard: TaskBoardSnapshot,
  live: boolean,
  language?: ChatResponseLanguage,
): AgentActivityItem | null {
  const total = taskBoard.tasks.length;
  if (total === 0) return null;
  const completed = taskBoard.tasks.filter(
    (task) => task.status === "completed" || task.status === "cancelled",
  ).length;
  const active = taskBoard.tasks.filter((task) => task.status === "in_progress").length;
  if (!live) {
    return {
      id: "task-board",
      label: t(language, "Updated task board", "작업 목록 업데이트"),
      detail: isKorean(language) ? `${completed}/${total}개 완료` : `${completed}/${total} complete`,
      status: "done",
    };
  }
  return {
    id: "task-board",
    label: active > 0
      ? isKorean(language)
        ? `${active}개 작업 진행 중`
        : `Working on ${active} ${plural(active, "task", "tasks")}`
      : t(language, "Updated task board", "작업 목록 업데이트"),
    detail: isKorean(language) ? `${completed}/${total}개 완료` : `${completed}/${total} complete`,
    status: active > 0 ? "running" : "done",
  };
}

function groupCompletedActivities(
  activities: ToolActivity[],
  language?: ChatResponseLanguage,
): AgentActivityItem[] {
  const counts: Record<ActivityCategory, number> = {
    command: 0,
    read: 0,
    knowledge: 0,
    other: 0,
  };

  for (const activity of activities) {
    counts[categorizeActivity(activity.label)] += 1;
  }

  const rows: AgentActivityItem[] = [];
  if (counts.command > 0) {
    rows.push({
      id: "completed-command",
      label: isKorean(language)
        ? `${counts.command}개 명령 실행`
        : `Ran ${counts.command} ${plural(counts.command, "command", "commands")}`,
      status: "done",
      actionCount: counts.command,
    });
  }
  if (counts.read > 0) {
    rows.push({
      id: "completed-read",
      label: isKorean(language)
        ? `${counts.read}개 파일 읽음`
        : `Read ${counts.read} ${plural(counts.read, "file", "files")}`,
      status: "done",
      actionCount: counts.read,
    });
  }
  if (counts.knowledge > 0) {
    rows.push({
      id: "completed-knowledge",
      label: isKorean(language)
        ? `지식 ${counts.knowledge}회 사용`
        : `Used knowledge ${counts.knowledge} ${plural(counts.knowledge, "time", "times")}`,
      status: "done",
      actionCount: counts.knowledge,
    });
  }
  if (counts.other > 0) {
    rows.push({
      id: "completed-other",
      label: isKorean(language)
        ? `${counts.other}개 작업 완료`
        : `Completed ${counts.other} ${plural(counts.other, "action", "actions")}`,
      status: "done",
      actionCount: counts.other,
    });
  }
  return rows;
}

export function deriveAgentActivityItems(input: DeriveAgentActivityInput): AgentActivityItem[] {
  const rows: AgentActivityItem[] = [];
  const language = input.responseLanguage;
  const now = input.now ?? Date.now();
  const liveElapsedSeconds = input.startedAt ? secondsBetween(input.startedAt, now) : undefined;

  if (input.fileProcessing) {
    rows.push({
      id: "file-processing",
      label: t(language, "Processing attachments", "첨부파일 처리 중"),
      status: "running",
    });
  }

  if (input.live && input.turnPhase) {
    const phaseRow = describePhase(input.turnPhase, language, liveElapsedSeconds);
    if (phaseRow) {
      rows.push({
        id: `phase-${input.turnPhase}`,
        status: "running",
        ...phaseRow,
        ...(input.thinkingContent ? { inputPreview: input.thinkingContent.slice(-500) } : {}),
      });
    }
  } else if (input.live && input.startedAt) {
    rows.push({
      id: "thinking",
      label: thinkingDurationLabel(secondsBetween(input.startedAt, now), language, false),
      status: "running",
      ...(input.thinkingContent ? { inputPreview: input.thinkingContent.slice(-500) } : {}),
    });
  } else if (!input.live && (input.thinkingDuration || input.thinkingContent)) {
    rows.push({
      id: "thought",
      label: input.thinkingDuration
        ? thinkingDurationLabel(input.thinkingDuration, language, true)
        : t(language, "Work", "작업"),
      status: "done",
      ...(input.thinkingContent ? { inputPreview: input.thinkingContent.slice(-500) } : {}),
    });
  }

  if (input.live && typeof input.pendingInjectionCount === "number" && input.pendingInjectionCount > 0) {
    rows.push({
      id: "pending-injections",
      label:
        input.pendingInjectionCount === 1
          ? t(language, "1 follow-up queued", "후속 메시지 1개 대기")
          : isKorean(language)
            ? `후속 메시지 ${input.pendingInjectionCount}개 대기`
            : `${input.pendingInjectionCount} follow-ups queued`,
      status: "running",
    });
  }

  if (
    input.live &&
    typeof input.heartbeatElapsedMs === "number" &&
    input.heartbeatElapsedMs > 0 &&
    !rows.some((row) => row.id === "phase-executing")
  ) {
    rows.push({
      id: "heartbeat",
      label: t(language, "Still working on current step", "현재 단계 계속 진행 중"),
      detail: formatSeconds(input.heartbeatElapsedMs / 1000, language),
      status: "running",
    });
  }

  if (input.taskBoard) {
    const row = summarizeTaskBoard(input.taskBoard, input.live === true, language);
    if (row) rows.push(row);
  }

  const activities = input.activities ?? [];
  const explicitRows: AgentActivityItem[] = [];
  const completed: ToolActivity[] = [];

  for (const activity of activities) {
    const status = input.live === true || activity.status !== "running" ? activity.status : "done";
    if (status === "running") {
      explicitRows.push({
        id: activity.id,
        label: isKorean(language) ? `${activity.label} 실행 중` : `Running ${activity.label}`,
        status: "running",
        durationMs: activity.durationMs,
        inputPreview: activity.inputPreview,
      });
    } else if (status === "error") {
      explicitRows.push({
        id: activity.id,
        label: isKorean(language) ? `${activity.label} 실패` : `${activity.label} failed`,
        status: "error",
        durationMs: activity.durationMs,
        outputPreview: activity.outputPreview,
      });
    } else if (status === "denied") {
      explicitRows.push({
        id: activity.id,
        label: isKorean(language) ? `${activity.label} 거부됨` : `${activity.label} denied`,
        status: "denied",
        durationMs: activity.durationMs,
      });
    } else {
      completed.push(activity);
    }
  }

  rows.push(...explicitRows);
  rows.push(...groupCompletedActivities(completed, language));
  return rows;
}

export function getAgentActivitySummary(
  items: AgentActivityItem[],
  language?: ChatResponseLanguage,
): string {
  if (items.length === 0) return "";
  if (items.length === 1) return items[0].label;
  const active = items.filter((item) => item.status === "running").length;
  const total = items.reduce((sum, item) => sum + (item.actionCount ?? 1), 0);
  if (active > 0) {
    return isKorean(language)
      ? `${total}개 작업 진행 중`
      : `${total} ${plural(total, "action", "actions")} in progress`;
  }
  return isKorean(language)
    ? `${total}개 작업 실행`
    : `Ran ${total} ${plural(total, "action", "actions")}`;
}

export function formatActivityDuration(durationMs?: number): string | null {
  if (typeof durationMs !== "number") return null;
  if (durationMs < 1000) return `${durationMs}ms`;
  return `${(durationMs / 1000).toFixed(1)}s`;
}
