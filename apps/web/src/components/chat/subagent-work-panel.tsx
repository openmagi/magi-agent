"use client";

import { useState } from "react";
import { deriveWorkConsoleRows, type WorkConsoleRow, type WorkConsoleRowStatus } from "@/chat-core";
import type { ChannelState, ChatResponseLanguage, SubagentProgressEvent } from "@/chat-core";

const SUBAGENT_PROGRESS_COLLAPSED_LIMIT = 8;
const MAIN_AGENT_TASK_ID = "main";
const EXPANDABLE_SNIPPET_CHARS = 96;

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

function statusDotClass(status: WorkConsoleRowStatus | SubagentProgressEvent["status"]): string {
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
      return "bg-secondary/30";
  }
}

function isUsefulSubagentRow(row: WorkConsoleRow): boolean {
  return !row.detail || !/^iteration\s+\d+$/i.test(row.detail.trim());
}

function taskIdFromSubagentRow(row: WorkConsoleRow): string {
  return row.id.replace(/^subagent:/, "");
}

function mainAgentStatusRow(rows: WorkConsoleRow[]): WorkConsoleRow | null {
  return rows.find((row) => row.status === "running" || row.status === "waiting") ?? rows[0] ?? null;
}

function renderWorkRowDetail(row: WorkConsoleRow): string | undefined {
  return row.detail ?? row.snippet;
}

function shouldUseExpandableSnippet(snippet: string): boolean {
  return snippet.length > EXPANDABLE_SNIPPET_CHARS || snippet.includes("\n");
}

function ProgressSnippet({ snippet }: { snippet: string }) {
  if (!shouldUseExpandableSnippet(snippet)) {
    return (
      <span className="mt-0.5 block whitespace-pre-wrap text-[11px] text-secondary/55">
        {snippet}
      </span>
    );
  }

  return (
    <details
      className="mt-1 rounded-md bg-black/[0.04] px-2 py-1.5 text-[10.5px] leading-snug text-secondary/70"
      data-work-console-row-details="true"
    >
      <summary className="cursor-pointer select-none text-[10px] font-medium uppercase tracking-wide text-secondary/45">
        Details
      </summary>
      <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words">
        {snippet}
      </pre>
    </details>
  );
}

function activeSubagentRowsFromChannelState(
  channelState: ChannelState,
  language?: ChatResponseLanguage,
): WorkConsoleRow[] {
  return deriveWorkConsoleRows({
    channelState,
    uiLanguage: language,
  }).filter(
    (row) =>
      row.group === "subagent" &&
      (row.status === "running" || row.status === "waiting") &&
      isUsefulSubagentRow(row),
  );
}

export function SubagentWorkPanel({
  rows,
  mainRows = [],
  channelState,
  language,
}: {
  rows: WorkConsoleRow[];
  mainRows?: WorkConsoleRow[];
  channelState: ChannelState;
  language?: ChatResponseLanguage;
}): React.ReactElement | null {
  const usefulRows = rows.filter(isUsefulSubagentRow);
  const hasMainRows = mainRows.length > 0;
  const firstTaskId = hasMainRows
    ? MAIN_AGENT_TASK_ID
    : usefulRows[0]
      ? taskIdFromSubagentRow(usefulRows[0])
      : null;
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(MAIN_AGENT_TASK_ID);
  const [expandedProgressTaskIds, setExpandedProgressTaskIds] = useState<Set<string>>(
    () => new Set(),
  );
  const selectedSubagentExists =
    !!selectedTaskId && usefulRows.some((row) => taskIdFromSubagentRow(row) === selectedTaskId);
  const effectiveTaskId =
    selectedTaskId === MAIN_AGENT_TASK_ID && hasMainRows
      ? MAIN_AGENT_TASK_ID
      : selectedSubagentExists
        ? selectedTaskId
        : firstTaskId;
  const mainSelected = effectiveTaskId === MAIN_AGENT_TASK_ID;
  const progress = effectiveTaskId
    ? (channelState.subagentProgress?.[effectiveTaskId] ?? [])
    : [];
  const selectedRow =
    mainSelected
      ? mainAgentStatusRow(mainRows)
      : usefulRows.find((row) => taskIdFromSubagentRow(row) === effectiveTaskId) ?? usefulRows[0];
  const mainStatus = mainAgentStatusRow(mainRows);
  const progressExpanded = effectiveTaskId
    ? expandedProgressTaskIds.has(effectiveTaskId)
    : false;
  const mainVisibleRows = progressExpanded
    ? mainRows
    : mainRows.slice(-SUBAGENT_PROGRESS_COLLAPSED_LIMIT);
  const mainHiddenCount = Math.max(0, mainRows.length - mainVisibleRows.length);
  const canExpandProgress = mainSelected
    ? mainRows.length > SUBAGENT_PROGRESS_COLLAPSED_LIMIT
    : progress.length > SUBAGENT_PROGRESS_COLLAPSED_LIMIT;
  const progressTotal = mainSelected ? mainRows.length : progress.length;
  const visibleProgress = progressExpanded
    ? progress
    : progress.slice(-SUBAGENT_PROGRESS_COLLAPSED_LIMIT);
  const hiddenProgressCount = mainSelected
    ? mainHiddenCount
    : Math.max(0, progress.length - visibleProgress.length);
  const toggleProgressExpanded = (taskId: string) => {
    setExpandedProgressTaskIds((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  };

  if (!hasMainRows && usefulRows.length === 0) return null;

  return (
    <div className="min-w-0" data-work-console-subagent-panel="true">
      <div className="flex gap-1.5 overflow-x-auto pb-1">
        {hasMainRows && (
          <button
            type="button"
            data-work-console-subagent-option={MAIN_AGENT_TASK_ID}
            onClick={() => setSelectedTaskId(MAIN_AGENT_TASK_ID)}
            className={`min-w-[8.5rem] max-w-[11rem] rounded-lg border px-2 py-2 text-left transition-colors ${
              mainSelected
                ? "border-[#7C3AED]/25 bg-[#7C3AED]/[0.06] shadow-[0_1px_4px_rgba(124,58,237,0.08)]"
                : "border-black/[0.06] bg-white/70 hover:bg-black/[0.03]"
            }`}
          >
            <span className="flex min-w-0 items-center gap-1.5">
              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(mainStatus?.status ?? "info")}`} />
              <span className="truncate text-[12px] font-semibold text-foreground/80">
                {t(language, "Main", "메인")}
              </span>
            </span>
            <span className="mt-0.5 block truncate text-[10px] text-secondary/45">
              {t(language, "current session", "현재 세션")}
            </span>
            {mainStatus && (
              <span className="mt-1 block truncate text-[10.5px] text-secondary/60">
                {renderWorkRowDetail(mainStatus) ?? mainStatus.label}
              </span>
            )}
          </button>
        )}
        {usefulRows.map((row) => {
          const taskId = taskIdFromSubagentRow(row);
          const selected = taskId === effectiveTaskId;
          return (
            <button
              key={row.id}
              type="button"
              data-work-console-subagent-option={taskId}
              onClick={() => setSelectedTaskId(taskId)}
              className={`min-w-[8.5rem] max-w-[11rem] rounded-lg border px-2 py-2 text-left transition-colors ${
                selected
                  ? "border-[#7C3AED]/25 bg-[#7C3AED]/[0.06] shadow-[0_1px_4px_rgba(124,58,237,0.08)]"
                  : "border-black/[0.06] bg-white/70 hover:bg-black/[0.03]"
              }`}
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(row.status)}`} />
                <span className="truncate text-[12px] font-semibold text-foreground/80">
                  {row.label}
                </span>
              </span>
              {row.meta && (
                <span className="mt-0.5 block truncate text-[10px] text-secondary/45">
                  {row.meta}
                </span>
              )}
              {row.detail && (
                <span className="mt-1 block truncate text-[10.5px] text-secondary/60">
                  {row.detail}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {effectiveTaskId && (
        <div
          className="mt-2 border-t border-black/[0.06] pt-2"
          data-work-console-subagent-progress-stream={effectiveTaskId}
          data-work-console-agent-progress-stream={effectiveTaskId}
        >
          <div className="mb-1.5 flex min-w-0 items-center justify-between gap-2">
            <div className="min-w-0 truncate text-[10px] font-semibold uppercase tracking-wide text-secondary/40">
              {mainSelected
                ? t(language, "Main session", "메인 세션")
                : selectedRow?.label ?? t(language, "Subagent", "서브에이전트")}
            </div>
            {canExpandProgress && (
              <button
                type="button"
                onClick={() => toggleProgressExpanded(effectiveTaskId)}
                className="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium text-primary/65 transition-colors hover:bg-[#7C3AED]/[0.06] hover:text-primary"
                aria-expanded={progressExpanded}
                data-work-console-subagent-progress-toggle={effectiveTaskId}
              >
                {progressExpanded
                  ? t(
                      language,
                      `Show latest ${SUBAGENT_PROGRESS_COLLAPSED_LIMIT}`,
                      `최근 ${SUBAGENT_PROGRESS_COLLAPSED_LIMIT}개 보기`,
                    )
                  : t(
                      language,
                      `Show all ${progressTotal} events`,
                      `${progressTotal}개 모두 보기`,
                    )}
              </button>
            )}
          </div>
          {mainSelected ? (
            mainVisibleRows.length > 0 ? (
              <ul className={progressExpanded ? "max-h-[22rem] space-y-1 overflow-y-auto pr-1" : "space-y-1"}>
                {mainVisibleRows.map((row) => (
                  <li
                    key={row.id}
                    className="flex min-w-0 items-start gap-2 rounded-md bg-black/[0.025] px-2 py-1.5"
                  >
                    <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(row.status)}`} />
                    <span className="min-w-0 flex-1">
                      <span className="flex min-w-0 items-baseline gap-2">
                        <span className="truncate text-[11.5px] font-medium text-foreground/80">
                          {row.label}
                        </span>
                        {row.meta && (
                          <span className="shrink-0 text-[10px] text-secondary/40">
                            {row.meta}
                          </span>
                        )}
                      </span>
                      {row.detail && (
                        <span className="mt-0.5 block truncate text-[11px] text-secondary/60">
                          {row.detail}
                        </span>
                      )}
                      {row.snippet && (
                        <ProgressSnippet snippet={row.snippet} />
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null
          ) : visibleProgress.length > 0 ? (
            <ul className={progressExpanded ? "max-h-[22rem] space-y-1 overflow-y-auto pr-1" : "space-y-1"}>
              {visibleProgress.map((event) => (
                <li
                  key={event.id}
                  className="flex min-w-0 items-start gap-2 rounded-md bg-black/[0.025] px-2 py-1.5"
                >
                  <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(event.status)}`} />
                  <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-baseline gap-2">
                      <span className="truncate text-[11.5px] font-medium text-foreground/80">
                        {event.label}
                      </span>
                      {event.durationMs !== undefined && (
                        <span className="shrink-0 text-[10px] text-secondary/40">
                          {Math.max(1, Math.round(event.durationMs / 1000))}s
                        </span>
                      )}
                    </span>
                    {event.detail && (
                      <span className="mt-0.5 block truncate text-[11px] text-secondary/60">
                        {event.detail}
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          ) : selectedRow?.detail ? (
            <div className="rounded-md bg-black/[0.025] px-2 py-1.5 text-[11px] text-secondary/60">
              {selectedRow.detail}
            </div>
          ) : null}
          {canExpandProgress && !progressExpanded && hiddenProgressCount > 0 && (
            <div
              className="mt-1 px-2 text-[10px] text-secondary/40"
              data-work-console-subagent-progress-hidden-count={effectiveTaskId}
            >
              {t(language, `${hiddenProgressCount} hidden`, `${hiddenProgressCount}개 숨김`)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function MobileSubagentStatusStrip({
  channelState,
  uiLanguage,
}: {
  channelState: ChannelState;
  uiLanguage?: ChatResponseLanguage;
}): React.ReactElement | null {
  const language = uiLanguage ?? channelState.responseLanguage;
  const rows = activeSubagentRowsFromChannelState(channelState, language);
  if (rows.length === 0) return null;

  const visibleRows = rows.slice(0, 3);
  const extraCount = rows.length - visibleRows.length;

  return (
    <div className="md:hidden px-4 pb-2" data-chat-mobile-subagent-strip="true">
      <div className="mx-auto flex max-w-3xl items-center gap-2 overflow-x-auto rounded-xl border border-emerald-500/15 bg-white/90 px-2.5 py-2 shadow-[0_1px_6px_rgba(16,185,129,0.08)]">
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-secondary/45">
          {t(language, "Agents", "에이전트")}
        </span>
        {visibleRows.map((row) => (
          <span
            key={row.id}
            className="inline-flex min-w-0 max-w-[9rem] shrink-0 items-center gap-1.5 rounded-md bg-black/[0.035] px-2 py-1"
            title={[row.label, row.meta, row.detail].filter(Boolean).join(" ")}
          >
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(row.status)}`} />
            <span className="truncate text-[11px] font-medium text-foreground/75">
              {row.label}
            </span>
          </span>
        ))}
        {extraCount > 0 && (
          <span className="shrink-0 rounded-md bg-black/[0.035] px-2 py-1 text-[11px] font-medium text-secondary/55">
            +{extraCount}
          </span>
        )}
      </div>
    </div>
  );
}
