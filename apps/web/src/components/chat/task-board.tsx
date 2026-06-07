"use client";

import { useState } from "react";
import type { TaskBoardSnapshot, TaskBoardTask } from "@/lib/chat/types";

interface TaskBoardProps {
  snapshot: TaskBoardSnapshot;
}

/**
 * Claude Code-style status glyph. Shapes mirror the CC TUI task list:
 *   pending     → ☐ hollow square
 *   in_progress → ◼ filled orange square (pulses)
 *   completed   → ✓ checkmark (title gets strikethrough at the row level)
 *   cancelled   → ⊘ muted with strikethrough
 */
function StatusIcon({ status }: { status: TaskBoardTask["status"] }) {
  if (status === "in_progress") {
    return (
      <span
        className="inline-flex h-3.5 w-3.5 items-center justify-center shrink-0"
        aria-label="in progress"
      >
        <span className="h-3 w-3 bg-[#F59E0B] rounded-[2px] animate-pulse" />
      </span>
    );
  }
  if (status === "completed") {
    return (
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-emerald-500 shrink-0"
        aria-label="completed"
      >
        <polyline points="5 12 10 17 19 7" />
      </svg>
    );
  }
  if (status === "cancelled") {
    return (
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        className="text-secondary/40 shrink-0"
        aria-label="cancelled"
      >
        <circle cx="12" cy="12" r="9" />
        <line x1="6.5" y1="17.5" x2="17.5" y2="6.5" />
      </svg>
    );
  }
  // pending
  return (
    <span
      className="inline-flex h-3.5 w-3.5 items-center justify-center shrink-0"
      aria-label="pending"
    >
      <span className="h-3 w-3 border-[1.5px] border-secondary/40 rounded-[2px]" />
    </span>
  );
}

function TaskRow({ task }: { task: TaskBoardTask }) {
  const isDone = task.status === "completed";
  const isCancelled = task.status === "cancelled";
  const isActive = task.status === "in_progress";
  const dim = isDone || isCancelled;

  return (
    <li className="flex items-start gap-2 py-0.5">
      <span className="mt-[3px]">
        <StatusIcon status={task.status} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span
            className={[
              "text-[13px] leading-snug",
              dim ? "text-secondary/50 line-through decoration-secondary/40" : "",
              isActive ? "text-foreground font-semibold" : "",
              !dim && !isActive ? "text-foreground" : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {task.title}
          </span>
          {task.parallelGroup && (
            <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-black/[0.04] text-secondary/60">
              {task.parallelGroup}
            </span>
          )}
        </div>
        {task.description && !dim && (
          <p className="text-[11.5px] mt-0.5 leading-snug text-secondary/60">
            {task.description}
          </p>
        )}
      </div>
    </li>
  );
}

/**
 * Claude Code-style task list.
 *
 * Layout:
 *   • Header row: "Tasks  N/M" with chevron to collapse the whole list
 *   • Active + pending tasks always visible
 *   • Completed/cancelled tasks: last COMPLETED_HEAD_COUNT shown inline,
 *     remainder folded behind "… +K completed" expandable row
 *
 * Behaviour mirrors the CC TUI agent task pane Kevin referenced —
 * strikethrough for done, orange square for in-progress, check for
 * completed, and the older-completed collapse so a 40-task plan
 * doesn't push the UI off-screen.
 */
const COMPLETED_HEAD_COUNT = 5;

export function TaskBoard({ snapshot }: TaskBoardProps) {
  const tasks = snapshot.tasks;
  const [expandCompleted, setExpandCompleted] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  if (tasks.length === 0) return null;

  const completed = tasks.filter(
    (t) => t.status === "completed" || t.status === "cancelled",
  );
  const active = tasks.filter((t) => t.status === "in_progress");
  const pending = tasks.filter((t) => t.status === "pending");
  const total = tasks.length;

  // Preserve original ordering within each bucket; chronologically the
  // snapshot is emitted in plan order so this keeps the visual match
  // with how the bot described the plan up front.
  const orderedActivePending = tasks.filter(
    (t) => t.status === "in_progress" || t.status === "pending",
  );
  const hiddenCompletedCount = Math.max(0, completed.length - COMPLETED_HEAD_COUNT);
  const visibleCompleted = expandCompleted
    ? completed
    : completed.slice(Math.max(0, completed.length - COMPLETED_HEAD_COUNT));

  return (
    <div className="mb-2 rounded-xl border border-black/[0.06] bg-white/60 px-3 py-2">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="flex items-center gap-2 w-full text-left cursor-pointer group"
      >
        {active.length > 0 ? (
          <span className="inline-flex h-3 w-3 items-center justify-center">
            <span className="h-2.5 w-2.5 bg-[#F59E0B] rounded-[2px] animate-pulse" />
          </span>
        ) : (
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="text-emerald-500/70"
          >
            <polyline points="5 12 10 17 19 7" />
          </svg>
        )}
        <span className="text-[11px] font-semibold uppercase tracking-wide text-secondary/70 group-hover:text-secondary">
          Tasks
        </span>
        <span className="text-[11px] text-secondary/40 tabular-nums">
          {completed.length}/{total}
        </span>
        {pending.length > 0 && (
          <span className="text-[11px] text-secondary/40">
            · {pending.length} pending
          </span>
        )}
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className={`ml-auto text-secondary/40 transition-transform duration-200 ${collapsed ? "" : "rotate-180"}`}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      {!collapsed && (
        <ul className="flex flex-col mt-1.5">
          {hiddenCompletedCount > 0 && !expandCompleted && (
            <li>
              <button
                type="button"
                onClick={() => setExpandCompleted(true)}
                className="text-[11.5px] text-secondary/50 hover:text-secondary/80 py-0.5 cursor-pointer"
              >
                … +{hiddenCompletedCount} completed
              </button>
            </li>
          )}
          {visibleCompleted.map((t) => (
            <TaskRow key={t.id} task={t} />
          ))}
          {orderedActivePending.map((t) => (
            <TaskRow key={t.id} task={t} />
          ))}
        </ul>
      )}
    </div>
  );
}
