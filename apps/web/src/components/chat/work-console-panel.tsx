"use client";

import { useEffect, useRef } from "react";
import {
  deriveWorkConsoleRows,
  type WorkConsoleRow,
  type WorkConsoleRowGroup,
  type WorkConsoleRowStatus,
} from "@/lib/chat/work-console";
import type {
  ChannelState,
  ChatResponseLanguage,
  ControlRequestRecord,
  QueuedMessage,
} from "@/lib/chat/types";

interface WorkConsolePanelProps {
  channelState: ChannelState;
  queuedMessages?: QueuedMessage[];
  controlRequests?: ControlRequestRecord[];
}

const GROUP_LABELS: Record<WorkConsoleRowGroup, string> = {
  status: "Now",
  tool: "Current steps",
  subagent: "Helpers",
  task: "Plan",
  queue: "Queued messages",
  control: "Needs input",
};

const GROUP_LABELS_KO: Record<WorkConsoleRowGroup, string> = {
  status: "현재",
  tool: "현재 단계",
  subagent: "도우미",
  task: "계획",
  queue: "대기 메시지",
  control: "입력 필요",
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

function sectionTone(
  group: WorkConsoleRowGroup,
): "status" | "agents" | "actions" | "queue" | "default" {
  if (group === "status") return "status";
  if (group === "subagent") return "agents";
  if (group === "tool") return "actions";
  if (group === "queue") return "queue";
  return "default";
}

function sectionClass(group: WorkConsoleRowGroup): string {
  switch (sectionTone(group)) {
    case "status":
      return "mb-3 min-h-0 rounded-xl border border-[#7C3AED]/15 bg-[#F8F6FF] p-2 shadow-[0_1px_6px_rgba(124,58,237,0.08)]";
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

function WorkConsoleAgentChip({ row }: { row: WorkConsoleRow }) {
  return (
    <li className="min-w-0 max-w-full">
      <div
        className="grid w-full min-w-0 grid-cols-[auto,minmax(0,1fr),auto] items-center gap-1 rounded-md border border-emerald-500/12 bg-emerald-50/35 px-1.5 py-1 text-[10.5px] leading-none"
        data-work-console-agent-chip="true"
        data-work-console-row-status={row.status}
        title={[row.label, row.meta, row.detail].filter(Boolean).join(" ")}
      >
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusClass(row.status)}`}
          aria-hidden="true"
        />
        <span className="min-w-0 truncate">
          <span className="font-medium text-foreground/80">{row.label}</span>
          {row.meta && (
            <span className="text-secondary/40"> {row.meta}</span>
          )}
        </span>
        {row.detail && (
          <span className="shrink-0 rounded bg-black/[0.04] px-1 py-0.5 text-[9px] font-medium text-secondary/55">
            {row.detail}
          </span>
        )}
      </div>
    </li>
  );
}

function WorkConsoleRowItem({ row }: { row: WorkConsoleRow }) {
  const isActionRow = row.group === "tool";
  const isStatusRow = row.group === "status";
  const isQueueRow = row.group === "queue";

  return (
    <li
      className={
        isStatusRow
          ? "flex min-w-0 items-start gap-2 rounded-lg border border-[#7C3AED]/20 bg-white/70 px-2.5 py-2.5 shadow-[0_1px_4px_rgba(124,58,237,0.08)]"
          : isActionRow
            ? `flex min-w-0 items-start gap-2 rounded-lg border px-2.5 py-2 ${actionRowToneClass(row.status)}`
            : isQueueRow
              ? "flex min-w-0 items-start gap-2 rounded-lg border border-amber-500/20 bg-white/70 px-2.5 py-2 shadow-[0_1px_4px_rgba(245,158,11,0.08)]"
              : "flex min-w-0 items-start gap-2 rounded-md px-2 py-1.5"
      }
      data-work-console-action-row={isActionRow ? "true" : undefined}
      data-work-console-status-row={isStatusRow ? "true" : undefined}
      data-work-console-queue-row={isQueueRow ? "true" : undefined}
      data-work-console-row-status={row.status}
    >
      <span
        className={`${isStatusRow ? "mt-2 h-2.5 w-2.5 ring-4 ring-[#7C3AED]/10" : isActionRow ? "mt-2 h-2 w-2" : "mt-1.5 h-1.5 w-1.5"} shrink-0 rounded-full ${statusClass(row.status)}`}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="min-w-0 truncate text-[12px] font-medium text-foreground/80">
            {row.label}
          </span>
          {row.meta && (
            <span className="shrink-0 text-[10px] text-secondary/40">{row.meta}</span>
          )}
        </div>
        {row.detail && (
          <p
            className={
              isStatusRow
                ? "mt-0.5 break-words text-[11.5px] leading-snug text-secondary/60"
                : isQueueRow
                  ? "mt-1 line-clamp-3 break-words text-[11.5px] leading-snug text-amber-950/75"
                : isActionRow
                ? "mt-1 line-clamp-2 break-words text-[11.5px] leading-snug text-secondary/65"
                : "mt-0.5 line-clamp-3 break-words text-[11px] leading-snug text-secondary/65"
            }
          >
            {row.detail}
          </p>
        )}
        {row.snippet && (
          <pre
            className={
              isActionRow
                ? "mt-2 max-h-20 overflow-auto rounded-md bg-black/[0.04] px-2 py-1.5 whitespace-pre-wrap break-words text-[10.5px] leading-snug text-secondary/70"
                : "mt-1 max-h-28 overflow-auto rounded-md bg-black/[0.04] px-2 py-1.5 whitespace-pre-wrap break-words text-[10.5px] leading-snug text-secondary/70"
            }
          >
            {row.snippet}
          </pre>
        )}
      </div>
    </li>
  );
}

export function WorkConsolePanel({
  channelState,
  queuedMessages = [],
  controlRequests = [],
}: WorkConsolePanelProps): React.ReactElement {
  const actionsListRef = useRef<HTMLUListElement | null>(null);
  const rows = deriveWorkConsoleRows({
    channelState,
    queuedMessages,
    controlRequests,
  });
  const language = channelState.responseLanguage;
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
                {groupRows.map((row) => (
                  isSubagentGroup ? (
                    <WorkConsoleAgentChip key={row.id} row={row} />
                  ) : (
                    <WorkConsoleRowItem key={row.id} row={row} />
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
