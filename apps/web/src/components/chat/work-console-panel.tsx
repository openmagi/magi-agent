"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  deriveWorkConsoleRows,
  type WorkConsoleRow,
  type WorkConsoleRowGroup,
  type WorkConsoleRowStatus,
} from "@/lib/chat/work-console";
import {
  WORK_CONSOLE_MOTION_TICK_MS,
  smoothedHeartbeatElapsedMs,
  workConsoleRowDelayMs,
} from "@/lib/chat/work-console-motion";
import type {
  ChannelState,
  BrowserFrame,
  ChatResponseLanguage,
  ControlRequestRecord,
  QueuedMessage,
} from "@/lib/chat/types";

interface WorkConsolePanelProps {
  channelState: ChannelState;
  queuedMessages?: QueuedMessage[];
  controlRequests?: ControlRequestRecord[];
  suppressInlineRunDetails?: boolean;
  uiLanguage?: ChatResponseLanguage;
}

const GROUP_LABELS: Record<WorkConsoleRowGroup, string> = {
  status: "Now",
  mission: "Missions",
  tool: "Current steps",
  subagent: "Helpers",
  task: "Plan",
  queue: "Queued messages",
  trace: "Runtime proof",
  control: "Needs input",
};

const GROUP_LABELS_KO: Record<WorkConsoleRowGroup, string> = {
  status: "현재",
  mission: "미션",
  tool: "현재 단계",
  subagent: "도우미",
  task: "계획",
  queue: "대기 메시지",
  trace: "런타임 증거",
  control: "입력 필요",
};

const INLINE_RUN_DETAIL_GROUPS = new Set<WorkConsoleRowGroup>([
  "tool",
  "subagent",
  "task",
  "queue",
  "trace",
  "control",
]);
const MAX_DISPLAY_GOAL_CHARS = 140;

type WorkConsoleMotionStyle = CSSProperties & {
  "--work-console-row-delay"?: string;
};

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

function groupLabel(group: WorkConsoleRowGroup, language?: ChatResponseLanguage): string {
  return isKorean(language) ? GROUP_LABELS_KO[group] : GROUP_LABELS[group];
}

function statusClass(status: WorkConsoleRowStatus): string {
  switch (status) {
    case "running":
      return "bg-[#7C3AED]";
    case "done":
      return "bg-emerald-500";
    case "waiting":
      return "bg-amber-500";
    case "error":
      return "bg-red-500";
    case "info":
    default:
      return "bg-secondary/25";
  }
}

function groupRows(rows: WorkConsoleRow[]): Array<[WorkConsoleRowGroup, WorkConsoleRow[]]> {
  const groups = new Map<WorkConsoleRowGroup, WorkConsoleRow[]>();
  for (const row of rows) {
    const existing = groups.get(row.group) ?? [];
    existing.push(row);
    groups.set(row.group, existing);
  }
  return Array.from(groups.entries());
}

function suppressInlineRunDetailRows(rows: WorkConsoleRow[]): WorkConsoleRow[] {
  return rows.filter((row) => !INLINE_RUN_DETAIL_GROUPS.has(row.group));
}

function compactDisplayGoal(value?: string | null): string | null {
  const normalized = value?.replace(/\s+/g, " ").trim();
  if (!normalized) return null;
  return normalized.length <= MAX_DISPLAY_GOAL_CHARS ? normalized : null;
}

function hasVisibleGoalMission(
  rows: WorkConsoleRow[],
  activeGoalMissionId?: string | null,
): boolean {
  const activeMissionRowId = activeGoalMissionId ? `mission:${activeGoalMissionId}` : null;
  return rows.some((row) => {
    if (row.group !== "mission") return false;
    if (activeMissionRowId && row.id === activeMissionRowId) return true;
    return row.meta?.split(/\s+/)[0] === "goal";
  });
}

function compactInlineOverviewRows(
  rows: WorkConsoleRow[],
  channelState: ChannelState,
  language?: ChatResponseLanguage,
): WorkConsoleRow[] {
  const visible = suppressInlineRunDetailRows(rows);
  const goal = hasVisibleGoalMission(visible, channelState.activeGoalMissionId)
    ? null
    : compactDisplayGoal(channelState.pendingGoalMissionTitle)
      ?? compactDisplayGoal(channelState.currentGoal);
  if (goal) {
    visible.push({
      id: "overview:goal",
      group: "status",
      label: t(language, "Goal", "목표"),
      detail: goal,
      status: "info",
    });
  }
  return visible;
}

function sectionTone(
  group: WorkConsoleRowGroup,
): "status" | "mission" | "agents" | "actions" | "queue" | "default" {
  if (group === "status") return "status";
  if (group === "mission") return "mission";
  if (group === "subagent") return "agents";
  if (group === "tool") return "actions";
  if (group === "queue") return "queue";
  return "default";
}

function sectionClass(group: WorkConsoleRowGroup): string {
  switch (sectionTone(group)) {
    case "status":
      return "mb-3 min-h-0 rounded-xl border border-[#7C3AED]/15 bg-[#F8F6FF] p-2 shadow-[0_1px_6px_rgba(124,58,237,0.08)]";
    case "mission":
      return "mb-3 min-h-0 rounded-xl border border-sky-500/20 bg-sky-50/70 p-2 shadow-[0_1px_6px_rgba(14,165,233,0.08)]";
    case "agents":
      return "mb-3 min-h-0 rounded-xl border border-emerald-500/20 bg-white p-2 shadow-[0_1px_6px_rgba(16,185,129,0.08)]";
    case "actions":
      return "mb-3 min-h-0 rounded-xl border border-black/[0.08] bg-white p-2 shadow-[0_1px_6px_rgba(15,23,42,0.06)]";
    case "queue":
      return "mb-3 min-h-0 rounded-xl border border-amber-500/25 bg-amber-50 p-2 shadow-[0_1px_6px_rgba(245,158,11,0.10)]";
    case "default":
    default:
      return "mb-2 min-h-0";
  }
}

function actionRowToneClass(status: WorkConsoleRowStatus): string {
  switch (status) {
    case "running":
      return "border-[#7C3AED]/20 bg-[#F7F4FF] shadow-[0_1px_6px_rgba(124,58,237,0.08)]";
    case "done":
      return "border-emerald-500/15 bg-emerald-50/50 shadow-[0_1px_4px_rgba(16,185,129,0.06)]";
    case "error":
      return "border-red-500/15 bg-red-50/60 shadow-[0_1px_4px_rgba(239,68,68,0.06)]";
    case "waiting":
      return "border-amber-500/15 bg-amber-50/60 shadow-[0_1px_4px_rgba(245,158,11,0.06)]";
    case "info":
    default:
      return "border-black/[0.06] bg-white/70 shadow-[0_1px_0_rgba(0,0,0,0.03)]";
  }
}

function runningMotionClass(status: WorkConsoleRowStatus): string {
  return status === "running" ? "work-console-running-row" : "";
}

function runningDotMotionClass(status: WorkConsoleRowStatus): string {
  return status === "running" ? "work-console-running-dot" : "";
}

function motionStyle(delayMs: number): WorkConsoleMotionStyle {
  return { "--work-console-row-delay": `${delayMs}ms` };
}

function useSmoothedChannelState(channelState: ChannelState): ChannelState {
  const [displayNowMs, setDisplayNowMs] = useState(() => Date.now());
  const heartbeatAnchorRef = useRef<{
    elapsedMs: number | null;
    observedAtMs: number;
  }>({
    elapsedMs: channelState.heartbeatElapsedMs ?? null,
    observedAtMs: displayNowMs,
  });
  const currentHeartbeatElapsedMs = channelState.heartbeatElapsedMs ?? null;

  if (heartbeatAnchorRef.current.elapsedMs !== currentHeartbeatElapsedMs) {
    heartbeatAnchorRef.current = {
      elapsedMs: currentHeartbeatElapsedMs,
      observedAtMs: displayNowMs,
    };
  }

  useEffect(() => {
    if (!channelState.streaming || currentHeartbeatElapsedMs === null) return;
    const tick = window.setInterval(() => {
      setDisplayNowMs(Date.now());
    }, WORK_CONSOLE_MOTION_TICK_MS);
    return () => window.clearInterval(tick);
  }, [channelState.streaming, currentHeartbeatElapsedMs]);

  const smoothedElapsedMs = smoothedHeartbeatElapsedMs(
    heartbeatAnchorRef.current.elapsedMs,
    heartbeatAnchorRef.current.observedAtMs,
    displayNowMs,
  );

  if (smoothedElapsedMs === currentHeartbeatElapsedMs) return channelState;
  return { ...channelState, heartbeatElapsedMs: smoothedElapsedMs };
}

function WorkConsoleAgentChip({
  row,
  motionDelayMs,
}: {
  row: WorkConsoleRow;
  motionDelayMs: number;
}) {
  return (
    <li
      className={`work-console-row-motion min-w-0 max-w-full ${runningMotionClass(row.status)}`}
      data-work-console-motion="true"
      style={motionStyle(motionDelayMs)}
    >
      <div
        className="grid w-full min-w-0 grid-cols-[auto,minmax(0,1fr),auto] items-center gap-1 rounded-md border border-emerald-500/12 bg-emerald-50/35 px-1.5 py-1 text-[10.5px] leading-none"
        data-work-console-agent-chip="true"
        data-work-console-row-status={row.status}
        title={[row.label, row.meta, row.detail].filter(Boolean).join(" ")}
      >
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusClass(row.status)} ${runningDotMotionClass(row.status)}`}
          aria-hidden="true"
        />
        <span className="min-w-0 truncate">
          <span
            key={row.label}
            className="work-console-text-motion font-medium text-foreground/80"
          >
            {row.label}
          </span>
          {row.meta && (
            <span key={row.meta} className="work-console-text-motion text-secondary/40">
              {" "}
              {row.meta}
            </span>
          )}
        </span>
        {row.detail && (
          <span
            key={row.detail}
            className="work-console-text-motion shrink-0 rounded bg-black/[0.04] px-1 py-0.5 text-[9px] font-medium text-secondary/55"
          >
            {row.detail}
          </span>
        )}
      </div>
    </li>
  );
}

function WorkConsoleRowItem({
  row,
  motionDelayMs,
}: {
  row: WorkConsoleRow;
  motionDelayMs: number;
}) {
  const isActionRow = row.group === "tool";
  const isStatusRow = row.group === "status";
  const isMissionRow = row.group === "mission";
  const isQueueRow = row.group === "queue";

  return (
    <li
      className={
        isStatusRow
          ? `work-console-row-motion flex min-w-0 items-start gap-2 rounded-lg border border-[#7C3AED]/20 bg-white/70 px-2.5 py-2.5 shadow-[0_1px_4px_rgba(124,58,237,0.08)] ${runningMotionClass(row.status)}`
          : isMissionRow
            ? `work-console-row-motion flex min-w-0 items-start gap-2 rounded-lg border border-sky-500/20 bg-white/75 px-2.5 py-2 shadow-[0_1px_4px_rgba(14,165,233,0.08)] ${runningMotionClass(row.status)}`
          : isActionRow
            ? `work-console-row-motion flex min-w-0 items-start gap-2 rounded-lg border px-2.5 py-2 ${actionRowToneClass(row.status)} ${runningMotionClass(row.status)}`
            : isQueueRow
              ? `work-console-row-motion flex min-w-0 items-start gap-2 rounded-lg border border-amber-500/20 bg-white/70 px-2.5 py-2 shadow-[0_1px_4px_rgba(245,158,11,0.08)] ${runningMotionClass(row.status)}`
              : `work-console-row-motion flex min-w-0 items-start gap-2 rounded-md px-2 py-1.5 ${runningMotionClass(row.status)}`
      }
      data-work-console-motion="true"
      data-work-console-action-row={isActionRow ? "true" : undefined}
      data-work-console-status-row={isStatusRow ? "true" : undefined}
      data-work-console-mission-row={isMissionRow ? "true" : undefined}
      data-work-console-queue-row={isQueueRow ? "true" : undefined}
      data-work-console-row-status={row.status}
      style={motionStyle(motionDelayMs)}
    >
      <span
        className={`${isStatusRow ? "mt-2 h-2.5 w-2.5 ring-4 ring-[#7C3AED]/10" : isMissionRow || isActionRow ? "mt-2 h-2 w-2" : "mt-1.5 h-1.5 w-1.5"} shrink-0 rounded-full ${statusClass(row.status)} ${runningDotMotionClass(row.status)}`}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <span
            key={row.label}
            className="work-console-text-motion min-w-0 truncate text-[12px] font-medium text-foreground/80"
          >
            {row.label}
          </span>
          {row.meta && (
            <span
              key={row.meta}
              className="work-console-text-motion shrink-0 text-[10px] text-secondary/40"
            >
              {row.meta}
            </span>
          )}
        </div>
        {row.detail && (
          <p
            key={row.detail}
            className={
              isStatusRow
                ? "work-console-text-motion mt-0.5 break-words text-[11.5px] leading-snug text-secondary/60"
                : isQueueRow
                  ? "work-console-text-motion mt-1 line-clamp-3 break-words text-[11.5px] leading-snug text-amber-950/75"
                : isActionRow
                ? "work-console-text-motion mt-1 line-clamp-2 break-words text-[11.5px] leading-snug text-secondary/65"
                : "work-console-text-motion mt-0.5 line-clamp-3 break-words text-[11px] leading-snug text-secondary/65"
            }
          >
            {row.detail}
          </p>
        )}
        {row.snippet && (
          <pre
            key={row.snippet}
            className={
              isActionRow
                ? "work-console-text-motion mt-2 max-h-20 overflow-auto rounded-md bg-black/[0.04] px-2 py-1.5 whitespace-pre-wrap break-words text-[10.5px] leading-snug text-secondary/70"
                : "work-console-text-motion mt-1 max-h-28 overflow-auto rounded-md bg-black/[0.04] px-2 py-1.5 whitespace-pre-wrap break-words text-[10.5px] leading-snug text-secondary/70"
            }
          >
            {row.snippet}
          </pre>
        )}
      </div>
    </li>
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

function BrowserFramePreview({
  frame,
  language,
}: {
  frame: BrowserFrame;
  language?: ChatResponseLanguage;
}) {
  const imageSrc = `data:${frame.contentType};base64,${frame.imageBase64}`;
  return (
    <section
      className="mb-3 overflow-hidden rounded-xl border border-black/[0.08] bg-white shadow-[0_1px_6px_rgba(15,23,42,0.06)]"
      data-work-console-browser-frame="true"
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
        className="block aspect-video w-full bg-black/[0.03] object-contain"
      />
    </section>
  );
}

export function WorkConsolePanel({
  channelState,
  queuedMessages = [],
  controlRequests = [],
  suppressInlineRunDetails = false,
  uiLanguage,
}: WorkConsolePanelProps): React.ReactElement {
  const actionsListRef = useRef<HTMLUListElement | null>(null);
  const smoothedChannelState = useSmoothedChannelState(channelState);
  const language = uiLanguage ?? smoothedChannelState.responseLanguage;
  const allRows = deriveWorkConsoleRows({
    channelState: smoothedChannelState,
    queuedMessages,
    controlRequests,
    uiLanguage: language,
  });
  const visibleRows = suppressInlineRunDetails
    ? compactInlineOverviewRows(allRows, smoothedChannelState, language)
    : allRows;
  const rows = visibleRows.length > 0
    ? visibleRows
    : [
        {
          id: "inline-stream",
          group: "status" as const,
          label: t(language, "Streaming in chat", "채팅에서 표시 중"),
          detail: t(
            language,
            "Live step details are shown inline in the conversation.",
            "실시간 단계 상세는 채팅 안에 표시됩니다.",
          ),
          status: "info" as const,
        },
      ];
  const groups = groupRows(rows);
  const actionRows = groups.find(([group]) => group === "tool")?.[1] ?? [];
  const lastActionId = actionRows[actionRows.length - 1]?.id ?? "";

  useEffect(() => {
    const el = actionsListRef.current;
    if (!el) return;

    const reduceMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({
      top: el.scrollHeight,
      behavior: reduceMotion ? "auto" : "smooth",
    });
  }, [actionRows.length, lastActionId]);

  return (
    <div className="flex min-h-0 flex-1 flex-col" aria-label={t(language, "Work in progress", "진행 중인 작업")}>
      <div className="border-b border-black/[0.06] px-3 py-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-secondary/70">
          {t(language, "Work in progress", "진행 중인 작업")}
        </div>
        <p className="mt-1 text-[11px] leading-snug text-secondary/45">
          {t(language, "Plain-language progress from the current run.", "현재 실행의 진행 상황입니다.")}
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {channelState.browserFrame && (
          <BrowserFramePreview frame={channelState.browserFrame} language={language} />
        )}
        {groups.map(([group, groupRows]) => {
          const isActionsGroup = group === "tool";
          const isSubagentGroup = group === "subagent";
          const tone = sectionTone(group);
          const isProminentGroup = tone !== "default";
          return (
            <section
              key={group}
              className={sectionClass(group)}
              data-work-console-group={group}
              data-work-console-section-tone={tone}
              data-work-console-section-density={isSubagentGroup ? "compact" : undefined}
            >
              <div
                className={
                  isProminentGroup
                    ? "mb-1.5 flex items-center justify-between px-1 text-[10px] font-semibold uppercase tracking-wide text-secondary/45"
                    : "mb-1 px-2 text-[10px] font-semibold uppercase tracking-wide text-secondary/40"
                }
              >
                <span>{groupLabel(group, language)}</span>
                {tone === "status" && (
                  <span className="rounded-full bg-[#7C3AED]/10 px-1.5 py-0.5 text-[9px] font-semibold text-[#7C3AED]">
                    {t(language, "Live", "실시간")}
                  </span>
                )}
                {tone === "actions" && (
                  <span className="rounded-full bg-black/[0.04] px-1.5 py-0.5 text-[9px] font-semibold text-secondary/45">
                    {groupRows.length}
                  </span>
                )}
                {tone === "mission" && (
                  <span className="rounded-full bg-sky-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-sky-800">
                    {groupRows.length} tracked
                  </span>
                )}
                {tone === "agents" && (
                  <span className="rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700">
                    {isKorean(language) ? `${groupRows.length}명` : `${groupRows.length} agents`}
                  </span>
                )}
                {tone === "queue" && (
                  <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-amber-800">
                    {isKorean(language) ? `${groupRows.length}개 대기` : `${groupRows.length} waiting`}
                  </span>
                )}
              </div>
              <ul
                ref={isActionsGroup ? actionsListRef : undefined}
                className={
                  isActionsGroup
                    ? "max-h-[44vh] space-y-0.5 overflow-y-auto overscroll-contain pr-1"
                    : isSubagentGroup
                      ? "grid grid-cols-2 gap-1.5"
                      : "space-y-0.5"
                }
                data-work-console-actions-scroll={isActionsGroup ? "bottom" : undefined}
                data-work-console-agent-roster={isSubagentGroup ? "compact" : undefined}
                data-work-console-agent-layout={isSubagentGroup ? "grid" : undefined}
                aria-label={
                  isActionsGroup
                    ? groupLabel("tool", language)
                    : isSubagentGroup
                      ? groupLabel("subagent", language)
                      : undefined
                }
              >
                {groupRows.map((row, index) => (
                  isSubagentGroup ? (
                    <WorkConsoleAgentChip
                      key={row.id}
                      row={row}
                      motionDelayMs={workConsoleRowDelayMs(index)}
                    />
                  ) : (
                    <WorkConsoleRowItem
                      key={row.id}
                      row={row}
                      motionDelayMs={workConsoleRowDelayMs(index)}
                    />
                  )
                ))}
              </ul>
            </section>
          );
        })}
      </div>
    </div>
  );
}
