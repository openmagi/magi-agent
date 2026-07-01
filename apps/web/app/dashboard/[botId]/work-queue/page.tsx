"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ListChecks, RefreshCw, RotateCcw, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { GlassCard } from "@/components/ui/glass-card";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import type { SelectOption } from "@/components/ui/select";
import { useAgentFetch } from "@/lib/local-api";
import {
  computePollDelayMs,
  fetchTasks,
  fetchEvents,
  fetchRuns,
  groupTasksByStatus,
  STATUS_COLUMNS,
} from "@/lib/work-queue-api";
import type { PollInterval, WorkQueueTask, WorkQueueEvent, WorkQueueRun } from "@/lib/work-queue-api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CORE_STATUS_COLUMNS = ["triage", "todo", "ready", "running", "completed", "blocked", "failed"] as const;
type CoreStatusColumn = (typeof CORE_STATUS_COLUMNS)[number];

const POLL_INTERVAL_OPTIONS: SelectOption[] = [
  { value: "5s", label: "Live 5s" },
  { value: "10s", label: "Live 10s" },
  { value: "30s", label: "Live 30s" },
  { value: "off", label: "Paused" },
];

const STATUS_FILTER_OPTIONS: SelectOption[] = [
  { value: "all", label: "All statuses" },
  ...STATUS_COLUMNS.map((s) => ({ value: s, label: s.charAt(0).toUpperCase() + s.slice(1) })),
];

const DISABLED_ERROR_MESSAGE = "Work-queue board is not enabled. Set MAGI_WORK_QUEUE_BOARD_API_ENABLED=1 on the runtime to view tasks.";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTimestamp(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function taskStatusVariant(status: string): "default" | "success" | "warning" | "error" | "gradient" {
  switch (status) {
    case "running":
      return "gradient";
    case "completed":
      return "success";
    case "failed":
      return "error";
    case "blocked":
      return "warning";
    default:
      return "default";
  }
}

function runStatusVariant(status: string, outcome: string | null): "default" | "success" | "warning" | "error" {
  if (outcome === "success") return "success";
  if (outcome === "failure" || status === "failed") return "error";
  if (status === "running") return "warning";
  return "default";
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface TaskCardProps {
  task: WorkQueueTask;
  onClick: () => void;
}

function TaskCard({ task, onClick }: TaskCardProps): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left rounded-xl border border-black/[0.06] bg-white/70 p-3 hover:border-primary/20 hover:bg-white transition-colors duration-200 cursor-pointer"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">{task.title}</p>
        {task.goal_mode ? (
          <RotateCcw className="h-3.5 w-3.5 shrink-0 text-primary-light" strokeWidth={2} aria-label="Goal mode" />
        ) : null}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Badge variant={taskStatusVariant(task.status)}>{task.status}</Badge>
        {task.consecutive_failures > 0 ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-red-500/20 bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-400">
            <AlertTriangle className="h-3 w-3" strokeWidth={2} />
            {task.consecutive_failures} failure{task.consecutive_failures !== 1 ? "s" : ""}
          </span>
        ) : null}
        {task.assignee ? (
          <span className="rounded-full border border-black/[0.08] bg-black/[0.04] px-2 py-0.5 text-xs text-secondary">
            {task.assignee}
          </span>
        ) : null}
      </div>
      <p className="mt-1.5 text-xs text-muted">{formatTimestamp(task.created_at)}</p>
    </button>
  );
}

interface StatusColumnCardProps {
  status: string;
  tasks: WorkQueueTask[];
  onTaskClick: (task: WorkQueueTask) => void;
}

function StatusColumnCard({ status, tasks, onTaskClick }: StatusColumnCardProps): React.ReactElement {
  return (
    <GlassCard className="min-h-[200px]">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold capitalize text-foreground">{status}</h2>
        <span className="rounded-full border border-black/[0.08] bg-black/[0.04] px-2 py-0.5 text-xs font-medium text-secondary">
          {tasks.length}
        </span>
      </div>
      {tasks.length === 0 ? (
        <p className="text-xs text-muted">No tasks</p>
      ) : (
        <div className="space-y-2">
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} onClick={() => { onTaskClick(task); }} />
          ))}
        </div>
      )}
    </GlassCard>
  );
}

interface TaskDetailModalProps {
  task: WorkQueueTask | null;
  open: boolean;
  onClose: () => void;
  agentFetch: ReturnType<typeof useAgentFetch>;
}

function TaskDetailModal({ task, open, onClose, agentFetch: agentFetchFn }: TaskDetailModalProps): React.ReactElement | null {
  const [events, setEvents] = useState<WorkQueueEvent[]>([]);
  const [runs, setRuns] = useState<WorkQueueRun[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const loadDetail = useCallback(async (id: string): Promise<void> => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      const [eventsData, runsData] = await Promise.all([
        fetchEvents(agentFetchFn, id),
        fetchRuns(agentFetchFn, id),
      ]);
      setEvents(eventsData);
      setRuns(runsData);
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "Failed to load task details.");
      setEvents([]);
      setRuns([]);
    } finally {
      setDetailLoading(false);
    }
  }, [agentFetchFn]);

  useEffect(() => {
    if (open && task) {
      void loadDetail(task.id);
    } else {
      setEvents([]);
      setRuns([]);
      setDetailError(null);
    }
  }, [open, task, loadDetail]);

  if (!task) return null;

  return (
    <Modal open={open} onClose={onClose} className="max-w-2xl">
      <div className="p-6 space-y-5">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-bold text-foreground leading-tight">{task.title}</h2>
            <p className="mt-1 text-xs font-mono text-muted">{task.id}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-lg p-1 text-secondary hover:bg-black/[0.04] hover:text-foreground transition-colors"
            aria-label="Close"
          >
            <svg viewBox="0 0 16 16" fill="none" className="h-4 w-4" aria-hidden="true">
              <path d="M3 3l10 10M13 3L3 13" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {/* Task fields */}
        <div className="rounded-xl border border-black/[0.06] bg-black/[0.025] p-4 space-y-2">
          <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
            <div>
              <span className="text-muted">Status</span>
              <div className="mt-0.5">
                <StatusBadge status={task.status} />
              </div>
            </div>
            <div>
              <span className="text-muted">Priority</span>
              <p className="mt-0.5 font-medium text-foreground">{task.priority}</p>
            </div>
            <div>
              <span className="text-muted">Assignee</span>
              <p className="mt-0.5 font-medium text-foreground">{task.assignee ?? "—"}</p>
            </div>
            <div>
              <span className="text-muted">Goal mode</span>
              <p className="mt-0.5 font-medium text-foreground">{task.goal_mode ? "Yes" : "No"}</p>
            </div>
            <div>
              <span className="text-muted">Consecutive failures</span>
              <p className={`mt-0.5 font-medium ${task.consecutive_failures > 0 ? "text-red-500" : "text-foreground"}`}>
                {task.consecutive_failures}
              </p>
            </div>
            <div>
              <span className="text-muted">Tenant</span>
              <p className="mt-0.5 font-medium text-foreground">{task.tenant ?? "—"}</p>
            </div>
            <div>
              <span className="text-muted">Created</span>
              <p className="mt-0.5 font-medium text-foreground">{formatTimestamp(task.created_at)}</p>
            </div>
            {task.idempotency_key ? (
              <div>
                <span className="text-muted">Idempotency key</span>
                <p className="mt-0.5 truncate font-mono text-secondary">{task.idempotency_key}</p>
              </div>
            ) : null}
          </div>
          {task.body ? (
            <div className="mt-2 border-t border-black/[0.06] pt-2">
              <span className="text-xs text-muted">Body</span>
              <p className="mt-1 text-xs text-secondary whitespace-pre-wrap">{task.body}</p>
            </div>
          ) : null}
          {task.result ? (
            <div className="mt-2 border-t border-black/[0.06] pt-2">
              <span className="text-xs text-muted">Result</span>
              <p className="mt-1 text-xs text-secondary whitespace-pre-wrap">{task.result}</p>
            </div>
          ) : null}
        </div>

        {/* Events + Runs */}
        {detailLoading ? (
          <div className="space-y-2">
            <div className="skeleton h-10" />
            <div className="skeleton h-10" />
          </div>
        ) : detailError ? (
          <p className="text-sm text-amber-700 rounded-xl border border-amber-500/20 bg-amber-500/10 px-4 py-3">
            {detailError}
          </p>
        ) : (
          <>
            {/* Events */}
            <div>
              <h3 className="mb-2 text-sm font-semibold text-foreground">
                Events{" "}
                <span className="ml-1 text-xs font-normal text-muted">({events.length})</span>
              </h3>
              {events.length === 0 ? (
                <p className="text-xs text-muted">No events recorded.</p>
              ) : (
                <div className="max-h-48 overflow-y-auto space-y-1.5 pr-1">
                  {events.map((event) => (
                    <div
                      key={event.id}
                      className="flex items-start justify-between gap-3 rounded-lg border border-black/[0.06] bg-white/70 px-3 py-2 text-xs"
                    >
                      <span className="font-semibold text-foreground">{event.kind}</span>
                      <span className="shrink-0 text-muted">{formatTimestamp(event.created_at)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Runs */}
            <div>
              <h3 className="mb-2 text-sm font-semibold text-foreground">
                Runs{" "}
                <span className="ml-1 text-xs font-normal text-muted">({runs.length})</span>
              </h3>
              {runs.length === 0 ? (
                <p className="text-xs text-muted">No runs recorded.</p>
              ) : (
                <div className="max-h-48 overflow-y-auto space-y-1.5 pr-1">
                  {runs.map((run) => (
                    <div
                      key={run.id}
                      className="rounded-lg border border-black/[0.06] bg-white/70 px-3 py-2 text-xs space-y-1"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <Badge variant={runStatusVariant(run.status, run.outcome)}>
                          {run.outcome ?? run.status}
                        </Badge>
                        <span className="text-muted">{formatTimestamp(run.started_at)}</span>
                      </div>
                      {run.summary ? <p className="text-secondary">{run.summary}</p> : null}
                      {run.error ? <p className="text-red-400">{run.error}</p> : null}
                      {run.ended_at ? (
                        <p className="text-muted">Ended: {formatTimestamp(run.ended_at)}</p>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function WorkQueuePage(): React.ReactElement {
  const agentFetch = useAgentFetch();
  const [tasks, setTasks] = useState<WorkQueueTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [statusFilter, setStatusFilter] = useState("all");
  const [selectedTask, setSelectedTask] = useState<WorkQueueTask | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [pollInterval, setPollInterval] = useState<PollInterval>("5s");
  const loadingRef = useRef(false);

  const loadTasks = useCallback(async (): Promise<void> => {
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    setDisabled(false);
    try {
      const opts = statusFilter !== "all" ? { status: statusFilter } : {};
      const data = await fetchTasks(agentFetch, opts);
      setTasks(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load tasks.";
      const isDisabled =
        message.includes("404") ||
        message.includes("not found") ||
        message.includes("Request failed: 404") ||
        message.toLowerCase().includes("not enabled");
      setDisabled(isDisabled);
      setError(isDisabled ? null : message);
      setTasks([]);
    } finally {
      setLoading(false);
      loadingRef.current = false;
    }
  }, [agentFetch, statusFilter]);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  // Auto-refresh polling. Three short-circuits keep this safe:
  //   (1) pollInterval === "off"   -> no setInterval
  //   (2) modalOpen                -> parent-list poll paused while the detail modal is open
  //   (3) loadingRef.current       -> skip ticks while a prior fetch is still in flight
  // The clearInterval cleanup ensures no leaked timers across re-renders or unmount.
  useEffect(() => {
    if (pollInterval === "off" || modalOpen) return;
    const delay = computePollDelayMs(pollInterval);
    if (delay == null) return;
    const id = setInterval(() => {
      if (loadingRef.current) return;
      void loadTasks();
    }, delay);
    return () => clearInterval(id);
  }, [pollInterval, modalOpen, loadTasks]);

  function openTaskModal(task: WorkQueueTask): void {
    setSelectedTask(task);
    setModalOpen(true);
  }

  function closeTaskModal(): void {
    setModalOpen(false);
    setSelectedTask(null);
  }

  const grouped = groupTasksByStatus(tasks);

  // Show core columns always; show archived only when it has tasks
  const visibleColumns: string[] = [
    ...CORE_STATUS_COLUMNS.filter((col): col is CoreStatusColumn => true),
    ...(grouped["archived"] && grouped["archived"].length > 0 ? ["archived"] : []),
  ];

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      {/* Page header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">
            Local runtime
          </div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
            <ListChecks className="h-6 w-6 text-primary-light" strokeWidth={2} />
            Work Queue
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-secondary">
            Kanban view of all tasks managed by the Magi Agent work queue. Read-only — click a task for details.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <div className="w-44">
            <Select
              options={STATUS_FILTER_OPTIONS}
              value={statusFilter}
              onChange={setStatusFilter}
              aria-label="Filter by status"
            />
          </div>
          <div className="w-32">
            <Select
              options={POLL_INTERVAL_OPTIONS}
              value={pollInterval}
              onChange={(v) => setPollInterval(v as PollInterval)}
              aria-label="Auto-refresh interval"
            />
          </div>
          <Button variant="secondary" size="sm" onClick={() => { void loadTasks(); }} disabled={loading}>
            <RefreshCw className="mr-2 h-4 w-4" strokeWidth={2} />
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
      </div>

      {/* Error banner */}
      {error ? (
        <div className="rounded-xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-700">
          {error}
        </div>
      ) : null}

      {/* Disabled / not-enabled state */}
      {disabled ? (
        <GlassCard>
          <div className="flex flex-col items-center py-12 text-center">
            <ListChecks className="mb-4 h-10 w-10 text-muted" strokeWidth={1.5} />
            <p className="text-sm font-medium text-foreground">Work-queue board is not enabled.</p>
            <p className="mt-2 text-xs text-secondary">
              Set{" "}
              <code className="rounded bg-black/[0.05] px-1.5 py-0.5 font-mono text-[11px]">
                MAGI_WORK_QUEUE_BOARD_API_ENABLED=1
              </code>{" "}
              on the runtime to view tasks.
            </p>
          </div>
        </GlassCard>
      ) : loading && tasks.length === 0 ? (
        /* Initial loading skeleton */
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {CORE_STATUS_COLUMNS.slice(0, 4).map((col) => (
            <GlassCard key={col}>
              <div className="mb-3 flex items-center justify-between">
                <div className="skeleton h-4 w-20" />
                <div className="skeleton h-4 w-6" />
              </div>
              <div className="space-y-2">
                <div className="skeleton h-16" />
                <div className="skeleton h-16" />
              </div>
            </GlassCard>
          ))}
        </div>
      ) : (
        /* Kanban board */
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {visibleColumns.map((col) => (
            <StatusColumnCard
              key={col}
              status={col}
              tasks={grouped[col] ?? []}
              onTaskClick={openTaskModal}
            />
          ))}
        </div>
      )}

      {/* Task detail modal */}
      <TaskDetailModal
        task={selectedTask}
        open={modalOpen}
        onClose={closeTaskModal}
        agentFetch={agentFetch}
      />
    </div>
  );
}
