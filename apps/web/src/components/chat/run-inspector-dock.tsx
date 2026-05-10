"use client";

import { useEffect, useState } from "react";
import { AgentActivityTimeline } from "./agent-activity-timeline";
import { TaskBoard } from "./task-board";
import { deriveWorkStateSummary, type WorkStateSummary } from "@/lib/chat/work-state";
import type {
  BrowserFrame,
  CitationGateStatus,
  ChannelState,
  ChatResponseLanguage,
  ControlRequestRecord,
  InspectedSource,
  QueuedMessage,
  SubagentActivity,
  TaskBoardSnapshot,
} from "@/lib/chat/types";

interface RunInspectorDockProps {
  channelState: ChannelState;
  queuedMessages?: QueuedMessage[];
  controlRequests?: ControlRequestRecord[];
  cancelHint?: string | null;
  defaultHidden?: boolean;
  compactDetails?: boolean;
  uiLanguage?: ChatResponseLanguage;
}

function openTaskBoard(snapshot?: TaskBoardSnapshot | null): TaskBoardSnapshot | null {
  if (!snapshot?.tasks.length) return null;
  if (!snapshot.tasks.some((task) => task.status === "pending" || task.status === "in_progress")) {
    return null;
  }
  return snapshot;
}

function pendingControlRequests(
  requests?: ControlRequestRecord[],
): ControlRequestRecord[] {
  return (requests ?? []).filter((request) => request.state === "pending");
}

function hasVisibleRunState(
  channelState: ChannelState,
  queuedMessages: QueuedMessage[],
  pendingRequests: ControlRequestRecord[],
  taskBoard: TaskBoardSnapshot | null,
  subagents: SubagentActivity[],
): boolean {
  const inspectedSources = channelState.inspectedSources ?? [];
  return (
    channelState.streaming ||
    (channelState.activeTools ?? []).length > 0 ||
    !!channelState.browserFrame ||
    subagents.length > 0 ||
    queuedMessages.length > 0 ||
    pendingRequests.length > 0 ||
    !!taskBoard ||
    inspectedSources.length > 0 ||
    !!channelState.citationGate
  );
}

function runIdentity(
  channelState: ChannelState,
  queuedMessages: QueuedMessage[],
  pendingRequests: ControlRequestRecord[],
  taskBoard: TaskBoardSnapshot | null,
  subagents: SubagentActivity[],
): string {
  const activeTools = channelState.activeTools ?? [];
  const inspectedSources = channelState.inspectedSources ?? [];
  const startedAt =
    channelState.thinkingStartedAt ??
    activeTools[0]?.startedAt ??
    channelState.browserFrame?.capturedAt ??
    subagents[0]?.startedAt ??
    inspectedSources[0]?.inspectedAt ??
    channelState.citationGate?.checkedAt ??
    null;
  if (startedAt !== null) return `run:${startedAt}`;

  if (pendingRequests.length > 0) {
    return `controls:${pendingRequests.map((request) => request.requestId).join(",")}`;
  }
  if (queuedMessages.length > 0) {
    return `queue:${queuedMessages.map((message) => message.id).join(",")}`;
  }
  if (taskBoard) {
    return `task-board:${taskBoard.tasks.map((task) => task.id).join(",")}`;
  }
  if (channelState.streaming) return "streaming";
  return "visible";
}

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

function phaseLabel(
  phase: ChannelState["turnPhase"],
  language?: ChatResponseLanguage,
): string | null {
  switch (phase) {
    case "pending":
      return t(language, "Preparing", "준비 중");
    case "planning":
      return t(language, "Planning", "계획 중");
    case "executing":
      return t(language, "Running", "실행 중");
    case "verifying":
      return t(language, "Verifying", "검증 중");
    case "committing":
      return t(language, "Writing answer", "답변 작성 중");
    case "aborted":
      return t(language, "Stopping", "중단 중");
    case "committed":
      return t(language, "Finishing", "마무리 중");
    default:
      return null;
  }
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

function subagentDisplayName(index: number): string {
  return SUBAGENT_NAMES[index % SUBAGENT_NAMES.length] ?? `Agent ${index + 1}`;
}

function subagentRoleLabel(role: string, language?: ChatResponseLanguage): string {
  const normalized = role.trim().toLowerCase();
  if (normalized === "explore" || normalized === "explorer" || normalized === "research") {
    return t(language, "explorer", "탐색");
  }
  if (normalized === "review" || normalized === "reviewer") return t(language, "reviewer", "검토");
  if (normalized === "worker" || normalized === "work") return t(language, "worker", "작업");
  return normalized || t(language, "subagent", "도우미");
}

function subagentStatusLabel(
  status: SubagentActivity["status"],
  language?: ChatResponseLanguage,
): string {
  const statusLanguage = language === "ko" ? "ko" : "en";
  switch (status) {
    case "waiting":
      return t(statusLanguage, "waiting", "승인 대기");
    case "done":
      return t(statusLanguage, "done", "완료");
    case "error":
      return t(statusLanguage, "failed", "실패");
    case "cancelled":
      return t(statusLanguage, "cancelled", "중단됨");
    case "running":
    default:
      return t(statusLanguage, "running", "작업 중");
  }
}

function subagentDotClass(status: SubagentActivity["status"]): string {
  switch (status) {
    case "waiting":
      return "bg-amber-500";
    case "done":
      return "bg-emerald-500";
    case "error":
    case "cancelled":
      return "bg-red-500";
    case "running":
    default:
      return "bg-[#7C3AED]";
  }
}

function BackgroundSubagents({
  subagents,
  language,
}: {
  subagents: SubagentActivity[];
  language?: ChatResponseLanguage;
}) {
  if (subagents.length === 0) return null;

  return (
    <div className="mt-2 border-t border-black/[0.06] pt-2" aria-label={t(language, "Background agents", "백그라운드 도우미")}>
      <div className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
        <span>{t(language, "Background agents", "백그라운드 도우미")}</span>
        <span className="font-medium normal-case tracking-normal text-secondary/35">
          {subagents.length}
        </span>
      </div>
      <div className="space-y-1">
        {subagents.map((subagent, index) => (
          <div key={subagent.taskId} className="flex min-w-0 items-center gap-2 text-xs">
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${subagentDotClass(subagent.status)}`}
              aria-hidden="true"
            />
            <span className="shrink-0 font-semibold text-secondary/80">
              {subagentDisplayName(index)}
            </span>
            <span className="shrink-0 text-secondary/45">
              ({subagentRoleLabel(subagent.role, language)})
            </span>
            <span className="shrink-0 text-secondary/55">-</span>
            <span className="shrink-0 text-secondary/70">
              {subagentStatusLabel(subagent.status, language)}
            </span>
            {subagent.detail && (
              <span className="min-w-0 truncate text-secondary/45">{subagent.detail}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function WorkStateSummaryRows({
  summary,
  language,
}: {
  summary: WorkStateSummary;
  language?: ChatResponseLanguage;
}) {
  const rows = [
    { label: t(language, "Goal", "목표"), value: summary.goal },
    { label: t(language, "Status", "상태"), value: summary.status },
    { label: t(language, "Progress", "진행"), value: summary.progress },
    { label: t(language, "Now", "현재"), value: summary.now },
    { label: t(language, "Next", "다음"), value: summary.next },
  ].filter((row): row is { label: string; value: string } => Boolean(row.value));

  return (
    <div className="mt-2 border-t border-black/[0.06] pt-2" aria-label={t(language, "Work state summary", "작업 상태 요약")}>
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
        {summary.title}
      </div>
      <dl className="grid gap-1.5 text-xs">
        {rows.map((row) => (
          <div key={row.label} className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-2">
            <dt className="shrink-0 font-medium text-secondary/45">{row.label}</dt>
            <dd className="min-w-0 break-words text-secondary/80">{row.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function browserActionLabel(action: string, language?: ChatResponseLanguage): string {
  switch (action) {
    case "open":
      return t(language, "Opening page", "페이지 여는 중");
    case "click":
    case "mouse_click":
      return t(language, "Clicking", "클릭 중");
    case "fill":
    case "keyboard_type":
    case "press":
      return t(language, "Typing", "입력 중");
    case "scroll":
      return t(language, "Scrolling", "스크롤 중");
    case "screenshot":
    case "snapshot":
      return t(language, "Inspecting page", "페이지 확인 중");
    case "scrape":
      return t(language, "Reading page", "페이지 읽는 중");
    default:
      return t(language, "Using browser", "브라우저 사용 중");
  }
}

function BrowserFrameInline({
  frame,
  language,
}: {
  frame: BrowserFrame;
  language?: ChatResponseLanguage;
}) {
  const imageSrc = `data:${frame.contentType};base64,${frame.imageBase64}`;
  return (
    <div
      className="mt-2 overflow-hidden rounded-lg border border-black/[0.08] bg-white"
      data-run-inspector-browser-frame="true"
    >
      <div className="flex min-w-0 items-center justify-between gap-2 border-b border-black/[0.06] px-2.5 py-1.5">
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-secondary/45">
          {t(language, "Live browser", "실시간 브라우저")}
        </span>
        <span className="min-w-0 truncate text-[10.5px] text-secondary/55">
          {browserActionLabel(frame.action, language)}
          {frame.url ? ` · ${frame.url}` : ""}
        </span>
      </div>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={imageSrc}
        alt={t(language, "Browser preview", "브라우저 미리보기")}
        className="block aspect-video w-full max-h-64 bg-black/[0.03] object-contain"
      />
    </div>
  );
}

function sourceKindLabel(kind: InspectedSource["kind"], language?: ChatResponseLanguage): string {
  switch (kind) {
    case "web_search":
      return t(language, "search", "검색");
    case "web_fetch":
      return t(language, "web", "웹");
    case "browser":
      return t(language, "browser", "브라우저");
    case "kb":
      return "KB";
    case "file":
      return t(language, "file", "파일");
    case "external_repo":
      return t(language, "repo", "저장소");
    case "external_doc":
      return t(language, "doc", "문서");
    case "subagent_result":
      return t(language, "helper", "도우미");
    default:
      return t(language, "source", "출처");
  }
}

function displaySourceUri(uri: string): string {
  try {
    const parsed = new URL(uri);
    return `${parsed.hostname}${parsed.pathname}${parsed.search}`.replace(/\/$/, "");
  } catch {
    return uri.replace(/^external:/, "");
  }
}

function citationStatusLabel(
  status: CitationGateStatus,
  language?: ChatResponseLanguage,
): string {
  switch (status.verdict) {
    case "ok":
      return t(language, "covered", "충족");
    case "violation":
      return t(language, "needs citations", "인용 필요");
    case "pending":
    default:
      return t(language, "checking", "확인 중");
  }
}

function citationStatusClass(status: CitationGateStatus): string {
  switch (status.verdict) {
    case "ok":
      return "bg-emerald-500";
    case "violation":
      return "bg-amber-500";
    case "pending":
    default:
      return "bg-secondary/35";
  }
}

function ResearchEvidence({
  sources,
  citationGate,
  language,
}: {
  sources: InspectedSource[];
  citationGate?: CitationGateStatus | null;
  language?: ChatResponseLanguage;
}) {
  if (sources.length === 0 && !citationGate) return null;
  const recentSources = sources.slice(-5).reverse();

  return (
    <div className="mt-2 border-t border-black/[0.06] pt-2" aria-label={t(language, "Research evidence", "리서치 근거")}>
      <div className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
        <span>{t(language, "Research evidence", "리서치 근거")}</span>
        {sources.length > 0 && (
          <span className="font-medium normal-case tracking-normal text-secondary/35">
            {isKorean(language) ? `출처 ${sources.length}개` : `${sources.length} sources`}
          </span>
        )}
      </div>

      {citationGate && (
        <div className="mb-1.5 flex min-w-0 items-center gap-2 text-xs">
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${citationStatusClass(citationGate)}`}
            aria-hidden="true"
          />
          <span className="shrink-0 font-medium text-secondary/80">
            {t(language, "Citation coverage", "인용 커버리지")}
          </span>
          <span className="shrink-0 text-secondary/50">
            {citationStatusLabel(citationGate, language)}
          </span>
          {citationGate.detail && (
            <span className="min-w-0 truncate text-secondary/55">{citationGate.detail}</span>
          )}
        </div>
      )}

      {recentSources.length > 0 && (
        <div className="space-y-1" aria-label={t(language, "Sources", "출처")}>
          <div className="sr-only">{t(language, "Sources", "출처")}</div>
          {recentSources.map((source) => (
            <div key={source.sourceId} className="flex min-w-0 items-center gap-2 text-xs">
              <span className="shrink-0 rounded bg-black/[0.04] px-1.5 py-0.5 font-mono text-[10px] text-secondary/55">
                {source.sourceId}
              </span>
              <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-secondary/40">
                {sourceKindLabel(source.kind, language)}
              </span>
              {source.title && (
                <span className="min-w-0 truncate font-medium text-secondary/80">
                  {source.title}
                </span>
              )}
              <span className="min-w-0 truncate text-secondary/50">
                {displaySourceUri(source.uri)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function RunInspectorDock({
  channelState,
  queuedMessages = [],
  controlRequests = [],
  cancelHint,
  defaultHidden = false,
  compactDetails = false,
  uiLanguage,
}: RunInspectorDockProps) {
  const language = uiLanguage ?? channelState.responseLanguage;
  const taskBoard = openTaskBoard(channelState.taskBoard);
  const subagents = channelState.subagents ?? [];
  const pendingRequests = pendingControlRequests(controlRequests);
  const visibleRunState = hasVisibleRunState(
    channelState,
    queuedMessages,
    pendingRequests,
    taskBoard,
    subagents,
  );
  const identity = runIdentity(channelState, queuedMessages, pendingRequests, taskBoard, subagents);
  const [hiddenRunIdentity, setHiddenRunIdentity] = useState<string | null>(() =>
    defaultHidden ? identity : null,
  );

  useEffect(() => {
    if (!visibleRunState || (hiddenRunIdentity !== null && hiddenRunIdentity !== identity)) {
      setHiddenRunIdentity(null);
    }
  }, [hiddenRunIdentity, identity, visibleRunState]);

  if (!visibleRunState) {
    return null;
  }

  if (hiddenRunIdentity === identity) {
    return (
      <div className="px-4 md:px-8 lg:px-12 pb-2" aria-label={t(language, "Current run hidden", "현재 실행 숨김")}>
        <div className="mx-auto flex max-w-3xl justify-end">
          <button
            type="button"
            onClick={() => setHiddenRunIdentity(null)}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-black/[0.08] bg-white/85 px-2.5 text-[11px] font-medium text-secondary/65 shadow-sm transition-colors hover:bg-white hover:text-secondary"
          >
            {t(language, "Show current run", "현재 실행 보기")}
          </button>
        </div>
      </div>
    );
  }

  const nextQueued = queuedMessages[0];
  const activeTools = channelState.activeTools ?? [];
  const inspectedSources = channelState.inspectedSources ?? [];
  const activeToolCount = activeTools.filter((activity) => activity.status === "running").length;
  const activeSubagentCount = subagents.filter(
    (subagent) => subagent.status === "running" || subagent.status === "waiting",
  ).length;
  const phase = phaseLabel(channelState.turnPhase ?? null, language);
  const workState = deriveWorkStateSummary({
    channelState,
    queuedMessages,
    controlRequests,
  });

  return (
    <div className="px-4 md:px-8 lg:px-12 pb-2" aria-label={t(language, "Current run", "현재 실행")}>
      <div className="mx-auto max-w-3xl rounded-lg border border-black/[0.08] bg-white/90 px-3 py-2.5 shadow-sm backdrop-blur">
        <div className="flex min-w-0 items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
              {t(language, "Current run", "현재 실행")}
            </div>
            <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-secondary/70">
              {channelState.reconnecting && (
                <span className="font-medium text-amber-600">
                  {t(language, "Reconnecting...", "다시 연결 중...")}
                </span>
              )}
              {phase && !channelState.reconnecting && <span>{phase}</span>}
              {activeToolCount > 0 && (
                <span>
                  {isKorean(language)
                    ? `${activeToolCount}개 실행 중`
                    : `${activeToolCount} active action${activeToolCount === 1 ? "" : "s"}`}
                </span>
              )}
              {activeSubagentCount > 0 && (
                <span>
                  {isKorean(language)
                    ? `${activeSubagentCount}명 백그라운드 작업 중`
                    : `${activeSubagentCount} background agent${activeSubagentCount === 1 ? "" : "s"}`}
                </span>
              )}
              {queuedMessages.length > 0 && (
                <span>
                  {isKorean(language)
                    ? `${queuedMessages.length}개 대기`
                    : `${queuedMessages.length} queued`}
                </span>
              )}
              {pendingRequests.length > 0 && (
                <span className="font-medium text-primary">
                  {t(language, "Needs approval", "승인 필요")}
                </span>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {cancelHint && (
              <div className="rounded-md bg-black/[0.04] px-2 py-1 text-[11px] font-medium text-secondary/70">
                {cancelHint}
              </div>
            )}
            <button
              type="button"
              aria-label={t(language, "Hide current run", "현재 실행 숨기기")}
              title={t(language, "Hide current run", "현재 실행 숨기기")}
              onClick={() => setHiddenRunIdentity(identity)}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-secondary/45 transition-colors hover:bg-black/[0.04] hover:text-secondary/80"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <WorkStateSummaryRows summary={workState} language={language} />

        {compactDetails ? null : (
        <div className="mt-2 max-h-[min(50vh,34rem)] overflow-y-auto overscroll-contain pr-1 [scrollbar-gutter:stable]">
          {channelState.browserFrame && (
            <BrowserFrameInline frame={channelState.browserFrame} language={language} />
          )}

          <ResearchEvidence
            sources={inspectedSources}
            citationGate={channelState.citationGate}
            language={language}
          />

          {(channelState.streaming || activeTools.length > 0) && (
            <div>
              <AgentActivityTimeline
                live={channelState.streaming}
                startedAt={channelState.thinkingStartedAt ?? null}
                fileProcessing={channelState.fileProcessing}
                turnPhase={channelState.turnPhase ?? null}
                heartbeatElapsedMs={channelState.heartbeatElapsedMs ?? null}
                pendingInjectionCount={channelState.pendingInjectionCount ?? 0}
                activities={activeTools}
                taskBoard={taskBoard}
                responseLanguage={language}
              />
            </div>
          )}

          <BackgroundSubagents subagents={subagents} language={language} />

          {pendingRequests.length > 0 && (
            <div className="mt-2 space-y-1 border-t border-black/[0.06] pt-2">
              {pendingRequests.slice(0, 3).map((request) => (
                <div key={request.requestId} className="flex min-w-0 items-center gap-2 text-xs">
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                  <span className="shrink-0 font-medium text-secondary/80">
                    {request.kind.replace("_", " ")}
                  </span>
                  <span className="min-w-0 truncate text-secondary/70">{request.prompt}</span>
                </div>
              ))}
            </div>
          )}

          {queuedMessages.length > 0 && (
            <div
              className="mt-2 rounded-xl border border-amber-500/25 bg-amber-50 px-3 py-2 text-xs text-amber-950 shadow-[0_1px_8px_rgba(245,158,11,0.10)]"
              data-run-inspector-queue-card="true"
            >
              <div className="mb-1 flex items-center justify-between gap-2 text-[10px] font-semibold uppercase tracking-wide text-amber-800/70">
                <span>{t(language, "Queued after current run", "현재 실행 후 전송 대기")}</span>
                <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[9px] text-amber-800">
                  {isKorean(language)
                    ? `${queuedMessages.length}개 대기`
                    : `${queuedMessages.length} waiting`}
                </span>
              </div>
              {nextQueued && (
                <div className="break-words text-amber-950/75">{nextQueued.content}</div>
              )}
            </div>
          )}

          {taskBoard && (
            <div className="mt-2 border-t border-black/[0.06] pt-2">
              <TaskBoard snapshot={taskBoard} />
            </div>
          )}
        </div>
        )}
      </div>
    </div>
  );
}
