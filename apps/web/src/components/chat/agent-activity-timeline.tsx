"use client";

import { useEffect, useState } from "react";
import {
  deriveAgentActivityItems,
  formatActivityDuration,
  getAgentActivitySummary,
  type AgentActivityItem,
} from "@/lib/chat/agent-activity";
import type { ChatResponseLanguage, TaskBoardSnapshot, ToolActivity } from "@/lib/chat/types";

interface AgentActivityTimelineProps {
  live?: boolean;
  startedAt?: number | null;
  thinkingContent?: string;
  thinkingDuration?: number;
  fileProcessing?: boolean;
  turnPhase?: "pending" | "planning" | "executing" | "verifying" | "committing" | "compacting" | "committed" | "aborted" | null;
  heartbeatElapsedMs?: number | null;
  pendingInjectionCount?: number;
  activities?: ToolActivity[];
  taskBoard?: TaskBoardSnapshot | null;
  responseLanguage?: ChatResponseLanguage;
  collapsedByDefault?: boolean;
}

function StatusDot({ status }: { status: AgentActivityItem["status"] }) {
  if (status === "running") {
    return (
      <span className="relative mt-[5px] flex h-2.5 w-2.5 shrink-0 items-center justify-center">
        <span className="absolute inline-flex h-full w-full rounded-full bg-[#7C3AED]/25 animate-ping" />
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[#7C3AED]/70" />
      </span>
    );
  }
  if (status === "error") {
    return (
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="mt-[3px] shrink-0 text-red-500" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
      </svg>
    );
  }
  if (status === "denied") {
    return (
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="mt-[3px] shrink-0 text-secondary/40" aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <line x1="7" y1="17" x2="17" y2="7" />
      </svg>
    );
  }
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="mt-[3px] shrink-0 text-emerald-500" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M5 12.5 10 17l9-10" />
    </svg>
  );
}

function ActivityDetails({ item }: { item: AgentActivityItem }) {
  const hasDetails = !!(item.inputPreview || item.outputPreview);
  if (!hasDetails) return null;
  return (
    <div className="ml-5 mt-1 space-y-1.5">
      {item.inputPreview && (
        <pre className="max-h-24 sm:max-h-32 overflow-auto rounded bg-black/[0.04] px-2 py-1.5 text-[11px] leading-snug text-secondary/75 whitespace-pre-wrap">
          {item.inputPreview}
        </pre>
      )}
      {item.outputPreview && (
        <pre className="max-h-24 sm:max-h-32 overflow-auto rounded bg-black/[0.04] px-2 py-1.5 text-[11px] leading-snug text-secondary/75 whitespace-pre-wrap">
          {item.outputPreview}
        </pre>
      )}
    </div>
  );
}

export function AgentActivityTimeline({
  live,
  startedAt,
  thinkingContent,
  thinkingDuration,
  fileProcessing,
  turnPhase,
  heartbeatElapsedMs,
  pendingInjectionCount,
  activities,
  taskBoard,
  responseLanguage,
  collapsedByDefault,
}: AgentActivityTimelineProps) {
  const [, forceTick] = useState(0);
  const rows = deriveAgentActivityItems({
    live,
    startedAt,
    thinkingContent,
    thinkingDuration,
    fileProcessing,
    turnPhase,
    heartbeatElapsedMs,
    pendingInjectionCount,
    activities,
    taskBoard,
    responseLanguage,
  });
  const summary = getAgentActivitySummary(rows, responseLanguage);
  const [expanded, setExpanded] = useState(live ? false : !(collapsedByDefault ?? true));

  useEffect(() => {
    if (!live || !startedAt) return;
    const id = window.setInterval(() => forceTick((value) => value + 1), 1000);
    return () => window.clearInterval(id);
  }, [live, startedAt]);

  if (rows.length === 0) return null;

  const summaryRow =
    rows[0]?.label === summary && (rows[0].id === "thinking" || rows[0].id === "thought")
      ? rows[0]
      : null;
  const visibleRows = summaryRow ? rows.slice(1) : rows;
  const summaryRowHasDetails = false;
  const hasExpandedContent = summaryRowHasDetails || visibleRows.length > 0;
  const canCollapse = hasExpandedContent && (visibleRows.length > 0 || summaryRowHasDetails || !live);
  const renderRows = () => (
    <div className="ml-1 border-l border-black/[0.06] pl-3">
      {summaryRowHasDetails && (
        <div className="py-1">
          <ActivityDetails item={summaryRow!} />
        </div>
      )}
      {visibleRows.map((row) => {
        const duration = formatActivityDuration(row.durationMs);
        return (
          <div key={row.id} className="py-1">
            <div className="flex min-w-0 items-start gap-2">
              <StatusDot status={row.status} />
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                  <span className="min-w-0 break-words text-secondary/75">{row.label}</span>
                  {row.detail && <span className="text-[11px] text-secondary/40">{row.detail}</span>}
                  {duration && <span className="ml-auto text-[11px] tabular-nums text-secondary/40">{duration}</span>}
                </div>
                <ActivityDetails item={row} />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );

  if (live) {
    return (
      <div className="mb-5 max-w-full border-b border-black/[0.08] pb-3 text-[14px] leading-relaxed text-secondary/70">
        <button
          type="button"
          onClick={() => canCollapse && setExpanded((value) => !value)}
          aria-expanded={expanded}
          className={`flex max-w-full items-center gap-2 py-1 text-left transition-colors ${canCollapse ? "cursor-pointer hover:text-secondary/90" : "cursor-default"}`}
        >
          <span className="min-w-0 truncate font-medium">{summary}</span>
          {canCollapse && (
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className={`shrink-0 text-secondary/40 transition-transform duration-150 ${expanded ? "rotate-90" : ""}`}
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="m9 18 6-6-6-6" />
            </svg>
          )}
        </button>
        {expanded && hasExpandedContent && <div className="mt-1">{renderRows()}</div>}
      </div>
    );
  }

  return (
    <div className="mb-5 max-w-full border-b border-black/[0.08] pb-3 text-[14px] leading-relaxed text-secondary/70">
      <button
        type="button"
        onClick={() => canCollapse && setExpanded((value) => !value)}
        aria-expanded={expanded}
        className={`flex max-w-full items-center gap-2 py-1 text-left transition-colors ${canCollapse ? "cursor-pointer hover:text-secondary/90" : "cursor-default"}`}
      >
        <span className="min-w-0 truncate font-medium">{live ? rows[0].label : summary}</span>
        {canCollapse && (
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className={`shrink-0 text-secondary/40 transition-transform duration-150 ${expanded ? "rotate-90" : ""}`}
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="m9 18 6-6-6-6" />
          </svg>
        )}
      </button>
      {expanded && hasExpandedContent && <div className="mt-1">{renderRows()}</div>}
    </div>
  );
}
