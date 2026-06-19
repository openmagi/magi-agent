/**
 * Work-queue API client for the Magi Agent OSS web dashboard.
 *
 * Provides TypeScript types, a pure `groupTasksByStatus` helper, and thin
 * async fetchers that call the work-queue REST endpoints via `agentFetch`.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface WorkQueueTask {
  id: string;
  title: string;
  status: string;
  created_at: number;
  body: string | null;
  assignee: string | null;
  priority: number;
  goal_mode: boolean;
  result: string | null;
  consecutive_failures: number;
  idempotency_key: string | null;
  tenant: string | null;
}

export interface WorkQueueEvent {
  id: number;
  task_id: string;
  run_id: number | null;
  kind: string;
  payload: unknown;
  created_at: number;
}

export interface WorkQueueRun {
  id: number;
  task_id: string;
  status: string;
  outcome: string | null;
  worker_pid: number | null;
  started_at: number;
  ended_at: number | null;
  summary: string | null;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Status columns
// ---------------------------------------------------------------------------

export const STATUS_COLUMNS = [
  "triage",
  "todo",
  "ready",
  "running",
  "completed",
  "blocked",
  "failed",
  "archived",
] as const;

export type StatusColumn = (typeof STATUS_COLUMNS)[number];

// ---------------------------------------------------------------------------
// Pure grouping helper
// ---------------------------------------------------------------------------

/**
 * Buckets a flat array of tasks into one array per known status column.
 * Every `STATUS_COLUMNS` entry is always present in the result (empty array
 * when no tasks have that status). Tasks with an unknown status are ignored.
 */
export function groupTasksByStatus(tasks: WorkQueueTask[]): Record<string, WorkQueueTask[]> {
  const result: Record<string, WorkQueueTask[]> = {};
  for (const col of STATUS_COLUMNS) {
    result[col] = [];
  }
  for (const task of tasks) {
    if (Object.prototype.hasOwnProperty.call(result, task.status)) {
      result[task.status].push(task);
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// AgentFetch type
// ---------------------------------------------------------------------------

export type AgentFetch = (path: string, init?: RequestInit) => Promise<Response>;

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

interface ErrorBody {
  error?: unknown;
}

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `Request failed: ${res.status}`;
    try {
      const body = (await res.json()) as ErrorBody;
      if (typeof body.error === "string" && body.error) {
        message = body.error;
      }
    } catch {
      // body not parseable; keep default message
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

function buildUrl(path: string, params: Record<string, string | number>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null);
  if (entries.length === 0) return path;
  const qs = entries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join("&");
  return `${path}?${qs}`;
}

// ---------------------------------------------------------------------------
// Fetchers
// ---------------------------------------------------------------------------

interface TasksResponse {
  tasks: WorkQueueTask[];
}

interface TaskResponse {
  task: WorkQueueTask;
}

interface EventsResponse {
  events: WorkQueueEvent[];
}

interface RunsResponse {
  runs: WorkQueueRun[];
}

export interface FetchTasksOptions {
  status?: string;
  limit?: number;
  offset?: number;
}

/**
 * Fetches a paginated list of tasks from the work-queue API.
 */
export async function fetchTasks(
  agentFetch: AgentFetch,
  opts: FetchTasksOptions = {},
): Promise<WorkQueueTask[]> {
  const params: Record<string, string | number> = {};
  if (opts.status !== undefined) params.status = opts.status;
  if (opts.limit !== undefined) params.limit = opts.limit;
  if (opts.offset !== undefined) params.offset = opts.offset;
  const url = buildUrl("/api/work-queue/v1/tasks", params);
  const res = await agentFetch(url);
  const data = await parseJson<TasksResponse>(res);
  return data.tasks;
}

/**
 * Fetches a single task by ID.
 */
export async function fetchTask(agentFetch: AgentFetch, id: string): Promise<WorkQueueTask> {
  const res = await agentFetch(`/api/work-queue/v1/tasks/${encodeURIComponent(id)}`);
  const data = await parseJson<TaskResponse>(res);
  return data.task;
}

/**
 * Fetches the event history for a task.
 */
export async function fetchEvents(agentFetch: AgentFetch, id: string): Promise<WorkQueueEvent[]> {
  const res = await agentFetch(`/api/work-queue/v1/tasks/${encodeURIComponent(id)}/events`);
  const data = await parseJson<EventsResponse>(res);
  return data.events;
}

/**
 * Fetches the run history for a task.
 */
export async function fetchRuns(agentFetch: AgentFetch, id: string): Promise<WorkQueueRun[]> {
  const res = await agentFetch(`/api/work-queue/v1/tasks/${encodeURIComponent(id)}/runs`);
  const data = await parseJson<RunsResponse>(res);
  return data.runs;
}
