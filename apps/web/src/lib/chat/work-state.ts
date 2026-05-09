import type {
  ChannelState,
  ControlRequestRecord,
  QueuedMessage,
  SubagentActivity,
  TaskBoardSnapshot,
  TaskBoardTask,
  ToolActivity,
  ChatResponseLanguage,
} from "./types";

export type WorkStateStatus = string;

export interface WorkStateSummary {
  title: string;
  goal: string;
  status: WorkStateStatus;
  progress?: string;
  now: string;
  next?: string;
}

export interface WorkStateInput {
  channelState: ChannelState;
  queuedMessages?: QueuedMessage[];
  controlRequests?: ControlRequestRecord[];
}

const PHASE_LABELS: Record<NonNullable<ChannelState["turnPhase"]>, string> = {
  pending: "Preparing",
  planning: "Planning",
  executing: "Running",
  verifying: "Verifying",
  committing: "Writing answer",
  committed: "Writing answer",
  aborted: "Stopping",
};

const PHASE_LABELS_KO: Record<NonNullable<ChannelState["turnPhase"]>, string> = {
  pending: "준비 중",
  planning: "계획 중",
  executing: "실행 중",
  verifying: "검증 중",
  committing: "답변 작성 중",
  committed: "답변 작성 중",
  aborted: "중단 중",
};

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

function pendingControlRequests(
  requests?: ControlRequestRecord[],
): ControlRequestRecord[] {
  return (requests ?? []).filter((request) => request.state === "pending");
}

function activeTools(channelState: ChannelState): ToolActivity[] {
  return (channelState.activeTools ?? []).filter((activity) => activity.status === "running");
}

function activeSubagents(channelState: ChannelState): SubagentActivity[] {
  return (channelState.subagents ?? []).filter(
    (subagent) => subagent.status === "running" || subagent.status === "waiting",
  );
}

function firstInProgressTask(taskBoard?: TaskBoardSnapshot | null): TaskBoardTask | null {
  return taskBoard?.tasks.find((task) => task.status === "in_progress") ?? null;
}

function completedLikeTaskCount(tasks: TaskBoardTask[]): number {
  return tasks.filter((task) => task.status === "completed" || task.status === "cancelled").length;
}

function dependenciesSatisfied(task: TaskBoardTask, tasks: TaskBoardTask[]): boolean {
  if (!task.dependsOn?.length) return true;
  const taskById = new Map(tasks.map((candidate) => [candidate.id, candidate]));
  return task.dependsOn.every((dependencyId) => {
    const dependency = taskById.get(dependencyId);
    return dependency?.status === "completed" || dependency?.status === "cancelled";
  });
}

function firstReadyPendingTask(taskBoard?: TaskBoardSnapshot | null): TaskBoardTask | null {
  if (!taskBoard?.tasks.length) return null;
  return (
    taskBoard.tasks.find(
      (task) => task.status === "pending" && dependenciesSatisfied(task, taskBoard.tasks),
    ) ??
    taskBoard.tasks.find((task) => task.status === "pending") ??
    null
  );
}

function controlRequestStatus(
  request: ControlRequestRecord,
  language?: ChatResponseLanguage,
): WorkStateStatus {
  return request.kind === "user_question"
    ? t(language, "Needs answer", "답변 필요")
    : t(language, "Needs approval", "승인 필요");
}

function controlRequestNow(
  request: ControlRequestRecord,
  language?: ChatResponseLanguage,
): string {
  switch (request.kind) {
    case "plan_approval":
      return t(language, "Waiting for plan approval", "계획 승인 대기 중");
    case "user_question":
      return t(language, "Waiting for your answer", "사용자 답변 대기 중");
    case "tool_permission":
    default:
      return t(language, "Waiting for tool permission", "도구 권한 대기 중");
  }
}

function statusFrom(
  channelState: ChannelState,
  pendingRequests: ControlRequestRecord[],
): WorkStateStatus {
  const language = channelState.responseLanguage;
  if (pendingRequests[0]) return controlRequestStatus(pendingRequests[0], language);
  if (channelState.reconnecting) return t(language, "Reconnecting", "다시 연결 중");
  if (channelState.turnPhase === "aborted" || channelState.error) {
    return t(language, "Blocked", "차단됨");
  }
  if ((channelState.activeTools ?? []).some((activity) => activity.status === "error")) {
    return t(language, "Blocked", "차단됨");
  }
  if (channelState.turnPhase === "verifying") return t(language, "Verifying", "검증 중");
  if (channelState.turnPhase === "committing" || channelState.turnPhase === "committed") {
    return t(language, "Writing answer", "답변 작성 중");
  }
  if (
    channelState.turnPhase === "executing" ||
    activeTools(channelState).length > 0 ||
    activeSubagents(channelState).length > 0 ||
    firstInProgressTask(channelState.taskBoard)
  ) {
    return t(language, "Running", "실행 중");
  }
  if (channelState.turnPhase === "pending" || channelState.turnPhase === "planning") {
    return t(language, "Planning", "계획 중");
  }
  return t(language, "Working", "작업 중");
}

function progressFrom(channelState: ChannelState): string | undefined {
  const language = channelState.responseLanguage;
  const tasks = channelState.taskBoard?.tasks ?? [];
  if (tasks.length > 0) {
    const completed = completedLikeTaskCount(tasks);
    return isKorean(language)
      ? `${completed}/${tasks.length}개 완료`
      : `${completed}/${tasks.length} tasks complete`;
  }

  const runningTools = activeTools(channelState);
  const runningSubagents = activeSubagents(channelState);
  if (runningTools.length > 0 && runningSubagents.length > 0) {
    const count = runningTools.length + runningSubagents.length;
    return isKorean(language) ? `${count}개 작업 실행 중` : `${count} actions active`;
  }
  if (runningTools.length > 0) {
    return isKorean(language)
      ? `${runningTools.length}개 작업 실행 중`
      : `${runningTools.length} action${runningTools.length === 1 ? "" : "s"} active`;
  }
  if (runningSubagents.length > 0) {
    if (isKorean(language)) return `${runningSubagents.length}명 백그라운드 작업 중`;
    return `${runningSubagents.length} background agent${
      runningSubagents.length === 1 ? "" : "s"
    } active`;
  }
  return undefined;
}

function phaseLabel(
  phase: ChannelState["turnPhase"],
  language?: ChatResponseLanguage,
): string {
  if (!phase) return t(language, "Working", "작업 중");
  return isKorean(language) ? PHASE_LABELS_KO[phase] : PHASE_LABELS[phase];
}

function goalFrom(channelState: ChannelState): string {
  return firstInProgressTask(channelState.taskBoard)?.title
    ?? t(channelState.responseLanguage, "Working on your request", "요청 처리 중");
}

function nowFrom(
  channelState: ChannelState,
  pendingRequests: ControlRequestRecord[],
): string {
  const language = channelState.responseLanguage;
  if (pendingRequests[0]) return controlRequestNow(pendingRequests[0], language);

  const task = firstInProgressTask(channelState.taskBoard);
  if (task) return task.title;

  const tool = activeTools(channelState)[0];
  if (tool) return tool.label;

  const subagent = activeSubagents(channelState)[0];
  if (subagent) {
    return subagent.detail || subagent.role || t(language, "Background agent", "백그라운드 도우미");
  }

  return phaseLabel(channelState.turnPhase ?? null, language);
}

function nextFrom(
  channelState: ChannelState,
  queuedMessages: QueuedMessage[],
  pendingRequests: ControlRequestRecord[],
): string | undefined {
  if (pendingRequests[0]) return pendingRequests[0].prompt;
  if (queuedMessages[0]) return queuedMessages[0].content;

  const task = firstReadyPendingTask(channelState.taskBoard);
  if (task) return task.title;

  if (channelState.turnPhase === "committing" || channelState.turnPhase === "committed") {
    return t(channelState.responseLanguage, "Preparing final answer", "최종 답변 준비 중");
  }
  return undefined;
}

export function deriveWorkStateSummary({
  channelState,
  queuedMessages = [],
  controlRequests = [],
}: WorkStateInput): WorkStateSummary {
  const pendingRequests = pendingControlRequests(controlRequests);

  return {
    title: t(channelState.responseLanguage, "Current Work", "현재 작업"),
    goal: goalFrom(channelState),
    status: statusFrom(channelState, pendingRequests),
    progress: progressFrom(channelState),
    now: nowFrom(channelState, pendingRequests),
    next: nextFrom(channelState, queuedMessages, pendingRequests),
  };
}
