/**
 * BackgroundTaskRegistry — T2-10.
 *
 * Tracks every `deliver="background"` SpawnAgent child so the parent
 * (and any subsequent turn in the same session) can query its progress,
 * fetch its final output, or abort it.
 *
 * Without this, a background spawn emits exactly one `spawn_result`
 * AgentEvent on completion and then the data is unrecoverable. Mirrors
 * Claude Code's AgentTool TaskList/TaskGet/TaskOutput/TaskStop surface.
 *
 * Storage:
 *   {workspaceRoot}/core-agent/bg-tasks/{taskId}.json
 *
 * Each mutation is persisted via tmp-rename atomic write. On construction
 * the registry scans the directory and rehydrates its in-memory map.
 *
 * AbortController instances are held ONLY in memory — they cannot be
 * persisted. After a pod restart any previously-running task is
 * reconciled to `status="failed"` (errorMessage="abandoned_by_restart")
 * on first touch, since the child loop is gone.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { atomicWriteJson } from "../storage/atomicWrite.js";

export type BackgroundTaskStatus = "running" | "completed" | "aborted" | "failed";

export interface BackgroundTaskProgress {
  at: number;
  label: string;
}

export interface BackgroundTaskArtifactRef {
  artifactId: string;
  kind: string;
  title: string;
  slug: string;
  l1Preview: string;
  importedFromArtifactId?: string;
}

export interface BackgroundTaskArtifacts {
  spawnDir: string;
  fileCount: number;
  handedOffArtifacts: BackgroundTaskArtifactRef[];
}

export interface BackgroundTaskRecord {
  taskId: string;
  parentTurnId: string;
  sessionKey: string;
  persona: string;
  prompt: string;
  status: BackgroundTaskStatus;
  startedAt: number;
  finishedAt?: number;
  resultText?: string;
  toolCallCount?: number;
  attempts?: number;
  error?: string;
  spawnDir?: string;
  artifacts?: BackgroundTaskArtifacts;
  progress?: BackgroundTaskProgress[];
}

export interface CreateBackgroundTaskInput {
  taskId: string;
  parentTurnId: string;
  sessionKey: string;
  persona: string;
  prompt: string;
  spawnDir?: string;
  abortController?: AbortController;
}

export interface BackgroundTaskListFilter {
  status?: BackgroundTaskStatus;
  sessionKey?: string;
  limit?: number;
  /** Cursor = taskId of the last-returned record. */
  cursor?: string;
}

export interface BackgroundTaskListPage {
  tasks: BackgroundTaskRecord[];
  nextCursor?: string;
}

/**
 * Patchable fields. Kept intentionally narrow — clients should never
 * overwrite `taskId` / `startedAt` / `sessionKey` / `parentTurnId`.
 */
export type BackgroundTaskPatch = Partial<
  Pick<
    BackgroundTaskRecord,
    | "status"
    | "finishedAt"
    | "resultText"
    | "toolCallCount"
    | "attempts"
    | "error"
    | "spawnDir"
    | "artifacts"
    | "progress"
  >
>;

export interface AttachResultInput {
  status: BackgroundTaskStatus;
  resultText?: string;
  toolCallCount?: number;
  attempts?: number;
  error?: string;
  artifacts?: BackgroundTaskArtifacts;
}

/**
 * Inline task notification (#81) — emitted into a parent session's
 * next LLM turn when a background task it spawned (or a cron-fired
 * turn, agent completion, etc.) finishes. See
 * `hooks/builtin/inlineTaskNotifier.ts`.
 *
 * Kept as a narrow, serializable shape so it survives being queued in
 * memory across turns. The `output` field is the completed task's
 * final text; the hook truncates it to 4KB when rendering so an
 * oversized subagent transcript doesn't blow the parent's context.
 */
export type TaskNotificationKind = "cron" | "spawn" | "agent";

export interface TaskNotification {
  taskId: string;
  sessionKey: string;
  kind: TaskNotificationKind;
  summary: string;
  output?: string;
  ts: number;
}

export class BackgroundTaskRegistry {
  private readonly dir: string;
  private readonly records = new Map<string, BackgroundTaskRecord>();
  private readonly aborts = new Map<string, AbortController>();
  private hydrated = false;
  /**
   * #81 — per-session FIFO queue of pending task-completion
   * notifications awaiting injection into the parent session's next
   * LLM turn. Keyed by sessionKey. Held only in memory: a pod restart
   * simply drops the unsent notifications (the completed task record
   * itself is still on disk for the `list`/`get` tools).
   */
  private readonly pendingNotifications = new Map<string, TaskNotification[]>();

  constructor(workspaceRoot: string) {
    this.dir = path.join(workspaceRoot, "core-agent", "bg-tasks");
  }

  /**
   * Load all persisted records from disk into the in-memory map. Safe
   * to call multiple times — subsequent calls are no-ops. The caller
   * should await this on startup; all mutating methods also lazy-call
   * it so tests don't have to remember.
   */
  async hydrate(): Promise<void> {
    if (this.hydrated) return;
    this.hydrated = true;
    try {
      await fs.mkdir(this.dir, { recursive: true });
    } catch {
      /* mkdir -p is best-effort; writes will surface real errors */
    }
    let entries: string[];
    try {
      entries = await fs.readdir(this.dir);
    } catch {
      return;
    }
    for (const name of entries) {
      if (!name.endsWith(".json")) continue;
      const full = path.join(this.dir, name);
      try {
        const raw = await fs.readFile(full, "utf8");
        const parsed = JSON.parse(raw) as BackgroundTaskRecord;
        if (parsed && typeof parsed.taskId === "string") {
          this.records.set(parsed.taskId, parsed);
        }
      } catch {
        // Ignore corrupt records; next write replaces the file.
      }
    }
  }

  async create(input: CreateBackgroundTaskInput): Promise<BackgroundTaskRecord> {
    await this.hydrate();
    const now = Date.now();
    const record: BackgroundTaskRecord = {
      taskId: input.taskId,
      parentTurnId: input.parentTurnId,
      sessionKey: input.sessionKey,
      persona: input.persona,
      prompt: input.prompt,
      status: "running",
      startedAt: now,
      ...(input.spawnDir ? { spawnDir: input.spawnDir } : {}),
    };
    this.records.set(record.taskId, record);
    if (input.abortController) {
      this.aborts.set(record.taskId, input.abortController);
    }
    await this.persist(record);
    return { ...record };
  }

  async update(
    taskId: string,
    patch: BackgroundTaskPatch,
  ): Promise<BackgroundTaskRecord | null> {
    await this.hydrate();
    const prev = this.records.get(taskId);
    if (!prev) return null;
    const next: BackgroundTaskRecord = { ...prev, ...patch };
    if (patch.progress !== undefined) {
      next.progress = [...patch.progress];
    }
    this.records.set(taskId, next);
    await this.persist(next);
    return { ...next };
  }

  async attachResult(
    taskId: string,
    result: AttachResultInput,
  ): Promise<BackgroundTaskRecord | null> {
    const updated = await this.update(taskId, {
      status: result.status,
      finishedAt: Date.now(),
      ...(result.resultText !== undefined ? { resultText: result.resultText } : {}),
      ...(result.toolCallCount !== undefined
        ? { toolCallCount: result.toolCallCount }
        : {}),
      ...(result.attempts !== undefined ? { attempts: result.attempts } : {}),
      ...(result.error !== undefined ? { error: result.error } : {}),
      ...(result.artifacts !== undefined ? { artifacts: result.artifacts } : {}),
    });

    // #81 — fire an inline notification for the parent session so the
    // next LLM turn sees a `<task-notification>` user-role block. We
    // keep this narrow (spawn only here); cron/agent-kind tasks call
    // `enqueueNotification` directly from their own completion paths.
    if (updated) {
      const summary =
        result.status === "completed"
          ? `spawn ${taskId} completed`
          : result.status === "aborted"
            ? `spawn ${taskId} aborted`
            : `spawn ${taskId} failed${result.error ? `: ${result.error}` : ""}`;
      this.enqueueNotification({
        taskId,
        sessionKey: updated.sessionKey,
        kind: "spawn",
        summary,
        ...(result.resultText ? { output: result.resultText } : {}),
        ts: Date.now(),
      });
    }
    return updated;
  }

  /**
   * #81 — enqueue a completion notification for the given session.
   * Public so non-spawn completion paths (cron fire, agent runs) can
   * post their own notifications. FIFO per session.
   */
  enqueueNotification(notification: TaskNotification): void {
    const queue = this.pendingNotifications.get(notification.sessionKey);
    if (queue) {
      queue.push(notification);
    } else {
      this.pendingNotifications.set(notification.sessionKey, [notification]);
    }
  }

  /**
   * #81 — drain and return pending notifications for the session.
   * Returns [] when none. Non-destructive when empty (no map entry
   * created).
   */
  drainForSession(sessionKey: string): TaskNotification[] {
    const queue = this.pendingNotifications.get(sessionKey);
    if (!queue || queue.length === 0) return [];
    this.pendingNotifications.delete(sessionKey);
    return queue;
  }

  /**
   * #81 — non-destructive peek, used by tests and diagnostic endpoints.
   */
  peekNotifications(sessionKey: string): TaskNotification[] {
    const queue = this.pendingNotifications.get(sessionKey);
    return queue ? [...queue] : [];
  }

  async recordProgress(
    taskId: string,
    label: string,
  ): Promise<BackgroundTaskRecord | null> {
    await this.hydrate();
    const prev = this.records.get(taskId);
    if (!prev) return null;
    const progress: BackgroundTaskProgress[] = [
      ...(prev.progress ?? []),
      { at: Date.now(), label: String(label).slice(0, 500) },
    ];
    // Cap to last 100 events to keep the JSON bounded.
    const trimmed = progress.length > 100 ? progress.slice(-100) : progress;
    const next: BackgroundTaskRecord = { ...prev, progress: trimmed };
    this.records.set(taskId, next);
    await this.persist(next);
    return { ...next };
  }

  async get(taskId: string): Promise<BackgroundTaskRecord | null> {
    await this.hydrate();
    const rec = this.records.get(taskId);
    return rec ? { ...rec } : null;
  }

  async list(filter: BackgroundTaskListFilter = {}): Promise<BackgroundTaskListPage> {
    await this.hydrate();
    const all = [...this.records.values()]
      .filter((r) => (filter.status ? r.status === filter.status : true))
      .filter((r) => (filter.sessionKey ? r.sessionKey === filter.sessionKey : true))
      .sort((a, b) => b.startedAt - a.startedAt);

    let startIdx = 0;
    if (filter.cursor) {
      const idx = all.findIndex((r) => r.taskId === filter.cursor);
      if (idx >= 0) startIdx = idx + 1;
    }
    const limit = filter.limit && filter.limit > 0 ? filter.limit : 50;
    const page = all.slice(startIdx, startIdx + limit).map((r) => ({ ...r }));
    const nextCursor =
      startIdx + limit < all.length && page.length > 0
        ? page[page.length - 1]?.taskId
        : undefined;
    return nextCursor ? { tasks: page, nextCursor } : { tasks: page };
  }

  /**
   * Trigger the child's abort signal, transition the record to
   * status="aborted". Returns true when an abort was actually fired
   * (a running task with a live controller), false otherwise.
   */
  async stop(taskId: string, reason?: string): Promise<boolean> {
    await this.hydrate();
    const prev = this.records.get(taskId);
    if (!prev) return false;
    if (prev.status !== "running") return false;
    const controller = this.aborts.get(taskId);
    let fired = false;
    if (controller && !controller.signal.aborted) {
      controller.abort();
      fired = true;
    }
    const next: BackgroundTaskRecord = {
      ...prev,
      status: "aborted",
      finishedAt: Date.now(),
      ...(reason ? { error: `stopped: ${reason}` } : {}),
    };
    this.records.set(taskId, next);
    this.aborts.delete(taskId);
    await this.persist(next);
    return fired || prev.status === "running";
  }

  /** Test / teardown hook — flushes controllers without touching disk. */
  clearControllers(): void {
    this.aborts.clear();
  }

  private async persist(record: BackgroundTaskRecord): Promise<void> {
    const file = path.join(this.dir, `${record.taskId}.json`);
    await atomicWriteJson(file, record);
  }
}
