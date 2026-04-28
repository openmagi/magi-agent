/**
 * TaskBoard — persistent per-session todo list.
 * Design reference: §7.1.
 *
 * The model uses this like Claude Code's TodoWrite: lay out tasks at
 * plan time, mark in_progress as work starts, completed as work
 * finishes. Tasks survive turn boundaries — a turn two prompts later
 * can still see / update prior tasks.
 *
 * Storage: one JSON file per session at
 *   {workspaceRoot}/core-agent/sessions/{sessionKeyHash}.tasks.json
 *
 * Phase 2d MVP — tool has 4 commands selected via `op`:
 *   - list    → returns the current board
 *   - create  → append new task(s)
 *   - update  → patch by id (status / parallelGroup / dependsOn / metadata)
 *   - complete→ shorthand: update(id, {status:"completed", completedAt:now})
 *
 * After every mutation a `task_board` AgentEvent is emitted with the
 * full board so the client can re-render without diffing.
 */

import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { atomicWriteJson } from "../storage/atomicWrite.js";
import { errorResult } from "../util/toolResult.js";

export interface TaskBoardEntry {
  id: string;
  title: string;
  description: string;
  status: "pending" | "in_progress" | "completed" | "cancelled";
  parallelGroup?: string;
  dependsOn?: string[];
  createdAt: number;
  startedAt?: number;
  completedAt?: number;
  metadata?: Record<string, unknown>;
}

/**
 * Structured long-loop state persisted on `TaskBoardEntry.metadata.iterationState`.
 * Ported from OMC's `iteration_state.json` (Port D, T3-13): long-running
 * tuning / research / tournament loops record `{round, step, strategy,
 * attempts, lastScore, approachFamily}` here so they survive pod restarts
 * without each skill reinventing state persistence.
 *
 * Canonical field: `TaskBoardEntry.metadata.iterationState`.
 */
export interface IterationState {
  /** Which iteration round we're in (1-based by convention). */
  round: number;
  /** Current step name e.g. "research" | "plan" | "execute" | "tournament". */
  step: string;
  /** Optional human-readable strategy / approach_family. */
  strategy?: string;
  /** Attempts made this round. */
  attempts: number;
  /** Most recent benchmark score (higher or lower = better is loop-defined). */
  lastScore?: number;
  /** OMC `approach_family` tag (caching / refactor / data-structure ...). */
  approachFamily?: string;
  /** ms since epoch when the loop first started. */
  startedAt: number;
  /** ms since epoch of the most recent bump. */
  updatedAt: number;
  /** Open for extension — skills can stash loop-specific state here.
   * The sweeper (`iterationStateSweeper`) reads `extra.workspaceRefs?: string[]`
   * to reconcile state against filesystem reality. */
  extra?: Record<string, unknown>;
}

export type TaskBoardOp =
  | { op: "list" }
  | {
      op: "create";
      tasks: Array<Omit<TaskBoardEntry, "id" | "status" | "createdAt"> & {
        status?: TaskBoardEntry["status"];
      }>;
    }
  | {
      op: "update";
      id: string;
      patch: Partial<Pick<
        TaskBoardEntry,
        "title" | "description" | "status" | "parallelGroup" | "dependsOn" | "metadata"
      >>;
    }
  | { op: "complete"; id: string }
  // T3-13 iterationState ergonomics (see `readIterationState` /
  // `writeIterationState` / `bumpIterationState` helpers below).
  | { op: "iteration_bump"; id: string; patch: Partial<IterationState> }
  | { op: "iteration_read"; id: string };

export interface TaskBoardInput {
  actions: TaskBoardOp[];
}

export interface TaskBoardOutput {
  tasks: TaskBoardEntry[];
  applied: number;
  /** Populated when the batch included an `iteration_read` op — returns
   * the last-read iteration state (null if absent / task not found). */
  iterationState?: IterationState | null;
}

export const VERIFICATION_TASK_RE = /verify|test|build|lint|qa|검증|테스트/i;

export function isVerificationTask(task: Pick<TaskBoardEntry, "title" | "description">): boolean {
  return VERIFICATION_TASK_RE.test(`${task.title}\n${task.description}`);
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    actions: {
      type: "array",
      minItems: 1,
      description:
        "A batched list of board ops. Each action is one of: " +
        "{op:'list'} | {op:'create', tasks:[...]} | {op:'update', id, patch} | {op:'complete', id} | " +
        "{op:'iteration_bump', id, patch} | {op:'iteration_read', id}.",
      items: {
        type: "object",
        properties: {
          op: {
            type: "string",
            enum: [
              "list",
              "create",
              "update",
              "complete",
              "iteration_bump",
              "iteration_read",
            ],
          },
          tasks: {
            type: "array",
            items: {
              type: "object",
              properties: {
                title: { type: "string" },
                description: { type: "string" },
                status: {
                  type: "string",
                  enum: ["pending", "in_progress", "completed", "cancelled"],
                },
                parallelGroup: { type: "string" },
                dependsOn: {
                  type: "array",
                  items: { type: "string" },
                },
                metadata: { type: "object", additionalProperties: true },
              },
              required: ["title", "description"],
              additionalProperties: true,
            },
          },
          id: { type: "string" },
          patch: {
            type: "object",
            properties: {
              title: { type: "string" },
              description: { type: "string" },
              status: {
                type: "string",
                enum: ["pending", "in_progress", "completed", "cancelled"],
              },
              parallelGroup: { type: "string" },
              dependsOn: {
                type: "array",
                items: { type: "string" },
              },
              metadata: { type: "object", additionalProperties: true },
              round: { type: "number" },
              step: { type: "string" },
              strategy: { type: "string" },
              attempts: { type: "number" },
              lastScore: { type: "number" },
              approachFamily: { type: "string" },
              extra: { type: "object", additionalProperties: true },
            },
            additionalProperties: true,
          },
        },
        required: ["op"],
        additionalProperties: false,
      },
    },
  },
  required: ["actions"],
  additionalProperties: false,
} as const;

export function taskFilePath(sessionsDir: string, sessionKey: string): string {
  const hash = crypto.createHash("sha1").update(sessionKey).digest("hex").slice(0, 16);
  return path.join(sessionsDir, `${hash}.tasks.json`);
}

export async function readBoard(file: string): Promise<TaskBoardEntry[]> {
  try {
    const raw = await fs.readFile(file, "utf8");
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed as TaskBoardEntry[];
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw err;
  }
}

export async function writeBoard(
  file: string,
  tasks: TaskBoardEntry[],
): Promise<void> {
  await atomicWriteJson(file, tasks);
}

function randomId(): string {
  // Short, sortable-ish task id. ULID-lite.
  const bytes = crypto.randomBytes(6).toString("hex");
  return `t_${Date.now().toString(36)}_${bytes}`;
}

export function applyOp(
  tasks: TaskBoardEntry[],
  op: TaskBoardOp,
): { next: TaskBoardEntry[]; changed: boolean } {
  if (op.op === "list") return { next: tasks, changed: false };

  if (op.op === "create") {
    const now = Date.now();
    const created: TaskBoardEntry[] = op.tasks.map((t) => ({
      id: randomId(),
      title: String(t.title ?? "").slice(0, 200),
      description: String(t.description ?? "").slice(0, 2_000),
      status: t.status ?? "pending",
      createdAt: now,
      ...(t.parallelGroup ? { parallelGroup: t.parallelGroup } : {}),
      ...(t.dependsOn ? { dependsOn: [...t.dependsOn] } : {}),
      ...(t.metadata ? { metadata: { ...t.metadata } } : {}),
    }));
    return { next: [...tasks, ...created], changed: created.length > 0 };
  }

  if (op.op === "update") {
    const idx = tasks.findIndex((t) => t.id === op.id);
    if (idx < 0) return { next: tasks, changed: false };
    const now = Date.now();
    const prev = tasks[idx];
    if (!prev) return { next: tasks, changed: false };
    const nextEntry: TaskBoardEntry = { ...prev, ...op.patch };
    if (op.patch.status === "in_progress" && !prev.startedAt) {
      nextEntry.startedAt = now;
    }
    if (op.patch.status === "completed" && !prev.completedAt) {
      nextEntry.completedAt = now;
    }
    const next = [...tasks];
    next[idx] = nextEntry;
    return { next, changed: true };
  }

  if (op.op === "complete") {
    return applyOp(tasks, {
      op: "update",
      id: op.id,
      patch: { status: "completed" },
    });
  }

  if (op.op === "iteration_bump") {
    const idx = tasks.findIndex((t) => t.id === op.id);
    if (idx < 0) return { next: tasks, changed: false };
    const prev = tasks[idx];
    if (!prev) return { next: tasks, changed: false };
    const now = Date.now();
    const prevState = readIterationStateFromEntry(prev);
    const merged = mergeIterationState(prevState, op.patch, now);
    const nextMetadata: Record<string, unknown> = {
      ...(prev.metadata ?? {}),
      iterationState: merged,
    };
    const next = [...tasks];
    next[idx] = { ...prev, metadata: nextMetadata };
    return { next, changed: true };
  }

  if (op.op === "iteration_read") {
    // Read-only — never mutates the board.
    return { next: tasks, changed: false };
  }

  return { next: tasks, changed: false };
}

/**
 * Extract `iterationState` from a TaskBoardEntry, if present. Returns
 * null when the entry has no metadata or no iterationState key.
 */
function readIterationStateFromEntry(entry: TaskBoardEntry): IterationState | null {
  const meta = entry.metadata;
  if (!meta || typeof meta !== "object") return null;
  const raw = (meta as Record<string, unknown>)["iterationState"];
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  const round = typeof obj["round"] === "number" ? (obj["round"] as number) : null;
  const step = typeof obj["step"] === "string" ? (obj["step"] as string) : null;
  const attempts =
    typeof obj["attempts"] === "number" ? (obj["attempts"] as number) : null;
  const startedAt =
    typeof obj["startedAt"] === "number" ? (obj["startedAt"] as number) : null;
  const updatedAt =
    typeof obj["updatedAt"] === "number" ? (obj["updatedAt"] as number) : null;
  if (round === null || step === null || attempts === null || startedAt === null || updatedAt === null) {
    return null;
  }
  const out: IterationState = { round, step, attempts, startedAt, updatedAt };
  if (typeof obj["strategy"] === "string") out.strategy = obj["strategy"] as string;
  if (typeof obj["lastScore"] === "number") out.lastScore = obj["lastScore"] as number;
  if (typeof obj["approachFamily"] === "string") {
    out.approachFamily = obj["approachFamily"] as string;
  }
  if (obj["extra"] && typeof obj["extra"] === "object" && !Array.isArray(obj["extra"])) {
    out.extra = { ...(obj["extra"] as Record<string, unknown>) };
  }
  return out;
}

/**
 * Merge a `Partial<IterationState>` patch onto the previous state (or
 * create a fresh state if none existed). `startedAt` is preserved on
 * existing states; `updatedAt` always becomes `now`. The `extra` object
 * is shallow-merged so callers can patch a single workspaceRef without
 * losing prior keys.
 */
function mergeIterationState(
  prev: IterationState | null,
  patch: Partial<IterationState>,
  now: number,
): IterationState {
  const base: IterationState = prev ?? {
    round: 0,
    step: "init",
    attempts: 0,
    startedAt: now,
    updatedAt: now,
  };
  const extra: Record<string, unknown> | undefined = patch.extra
    ? { ...(base.extra ?? {}), ...patch.extra }
    : base.extra;
  const merged: IterationState = {
    round: patch.round ?? base.round,
    step: patch.step ?? base.step,
    attempts: patch.attempts ?? base.attempts,
    startedAt: base.startedAt,
    updatedAt: now,
  };
  const strategy = patch.strategy ?? base.strategy;
  if (strategy !== undefined) merged.strategy = strategy;
  const lastScore = patch.lastScore ?? base.lastScore;
  if (lastScore !== undefined) merged.lastScore = lastScore;
  const approachFamily = patch.approachFamily ?? base.approachFamily;
  if (approachFamily !== undefined) merged.approachFamily = approachFamily;
  if (extra !== undefined) merged.extra = extra;
  return merged;
}

/**
 * Read the iterationState for a given task id from the board file.
 * Returns null when the board does not exist, the task is not found,
 * or the task has no iterationState metadata. File errors propagate.
 */
export async function readIterationState(
  file: string,
  taskId: string,
): Promise<IterationState | null> {
  const tasks = await readBoard(file);
  const entry = tasks.find((t) => t.id === taskId);
  if (!entry) return null;
  return readIterationStateFromEntry(entry);
}

/**
 * Write a fresh iterationState for a task, replacing any prior state.
 * No-op (does NOT throw) if the task is not found — the caller may be
 * racing a sweep; writing a state onto a vanished task would create
 * ghost metadata. Returns nothing; use `readIterationState` to verify.
 * Persists via the TaskBoard tmp-rename atomic write.
 */
export async function writeIterationState(
  file: string,
  taskId: string,
  next: IterationState,
): Promise<void> {
  const tasks = await readBoard(file);
  const idx = tasks.findIndex((t) => t.id === taskId);
  if (idx < 0) return;
  const prev = tasks[idx];
  if (!prev) return;
  const nextMetadata: Record<string, unknown> = {
    ...(prev.metadata ?? {}),
    iterationState: next,
  };
  const updated = [...tasks];
  updated[idx] = { ...prev, metadata: nextMetadata };
  await writeBoard(file, updated);
}

/**
 * Read-modify-write helper. Loads the current iterationState (or
 * synthesises a default), merges the patch, and persists. Returns the
 * resulting state.
 *
 * No-op / returns a synthetic default when the task does not exist; the
 * caller can inspect `result.updatedAt === 0` as a sentinel if strict
 * existence is required.
 *
 * Atomicity: relies on TaskBoard's tmp-rename write. Concurrent bumps
 * against the SAME task can still race — writers should serialise per
 * taskId if strict ordering matters. The test suite verifies that
 * concurrent bumps never corrupt the underlying JSON.
 */
export async function bumpIterationState(
  file: string,
  taskId: string,
  patch: Partial<IterationState>,
): Promise<IterationState> {
  const tasks = await readBoard(file);
  const idx = tasks.findIndex((t) => t.id === taskId);
  const now = Date.now();
  if (idx < 0) {
    // Sentinel state — task not found. Caller should treat as no-op.
    return {
      round: 0,
      step: "missing",
      attempts: 0,
      startedAt: 0,
      updatedAt: 0,
    };
  }
  const prev = tasks[idx];
  if (!prev) {
    return {
      round: 0,
      step: "missing",
      attempts: 0,
      startedAt: 0,
      updatedAt: 0,
    };
  }
  const prevState = readIterationStateFromEntry(prev);
  const merged = mergeIterationState(prevState, patch, now);
  const nextMetadata: Record<string, unknown> = {
    ...(prev.metadata ?? {}),
    iterationState: merged,
  };
  const updated = [...tasks];
  updated[idx] = { ...prev, metadata: nextMetadata };
  await writeBoard(file, updated);
  return merged;
}

export function makeTaskBoardTool(sessionsDir: string): Tool<TaskBoardInput, TaskBoardOutput> {
  return {
    name: "TaskBoard",
    description:
      "Manage a persistent to-do list for this session. `list` reads; `create` adds tasks (each needs title + description, optional parallelGroup / dependsOn); `update` patches a task by id (status pending→in_progress→completed/cancelled, or `metadata: { iterationState: {...} }` for long-loop state); `complete` shortcut marks a task completed; `iteration_bump` merges a partial IterationState patch onto a task (fields: round, step, strategy, attempts, lastScore, approachFamily, extra); `iteration_read` returns the task's current IterationState in the response's `iterationState` field. Use it to plan multi-step work, declare dependencies, persist long-running loop state across pod restarts, and show progress to the user. Board persists across turns.",
    inputSchema: INPUT_SCHEMA,
    permission: "meta",
    async execute(
      input: TaskBoardInput,
      ctx: ToolContext,
    ): Promise<ToolResult<TaskBoardOutput>> {
      const start = Date.now();
      try {
        // Defensive parse: Claude occasionally stringifies the
        // `actions` array ({"actions":"[...]"} instead of
        // {"actions":[...]}). Accept both shapes.
        let actions: TaskBoardOp[];
        if (Array.isArray(input.actions)) {
          actions = input.actions;
        } else if (typeof input.actions === "string") {
          try {
            const parsed = JSON.parse(input.actions);
            if (!Array.isArray(parsed)) {
              return {
                status: "error",
                errorCode: "bad_input",
                errorMessage: "`actions` must be an array",
                durationMs: Date.now() - start,
              };
            }
            actions = parsed as TaskBoardOp[];
          } catch (err) {
            return {
              status: "error",
              errorCode: "bad_input",
              errorMessage: `could not parse stringified actions: ${(err as Error).message}`,
              durationMs: Date.now() - start,
            };
          }
        } else {
          return {
            status: "error",
            errorCode: "bad_input",
            errorMessage: "`actions` must be an array",
            durationMs: Date.now() - start,
          };
        }

        const file = taskFilePath(sessionsDir, ctx.sessionKey);
        let tasks = await readBoard(file);
        let applied = 0;
        let mutated = false;
        let lastIterationRead: IterationState | null | undefined;
        for (const op of actions) {
          const { next, changed } = applyOp(tasks, op);
          tasks = next;
          if (op.op === "iteration_read") {
            // Read-only — return the requested task's state (or null
            // if absent). No mutation, so `applied` is still bumped
            // below for consistency with `list`.
            const entry = tasks.find((t) => t.id === op.id);
            lastIterationRead = entry ? readIterationStateFromEntry(entry) : null;
            // No counted application — mirrors `list` (zero-changed,
            // zero-applied) so the caller can distinguish reads.
            continue;
          }
          if (changed) {
            applied++;
            mutated = true;
          } else if (op.op !== "list") {
            applied++;
          }
        }
        if (mutated) {
          await writeBoard(file, tasks);
          // Push a full-board snapshot onto the §7.9 AgentEvent stream
          // so the client's <TaskBoard> component can re-render without
          // a separate fetch.
          ctx.emitAgentEvent?.({
            type: "task_board",
            tasks: tasks.map((t) => ({
              id: t.id,
              title: t.title,
              description: t.description,
              status: t.status,
              ...(t.parallelGroup ? { parallelGroup: t.parallelGroup } : {}),
              ...(t.dependsOn ? { dependsOn: t.dependsOn } : {}),
            })),
          });
          await ctx.emitControlEvent?.({
            type: "task_board_snapshot",
            turnId: ctx.turnId,
            taskBoard: {
              tasks: tasks.map((t) => ({
                id: t.id,
                title: t.title,
                description: t.description,
                status: t.status,
                ...(t.parallelGroup ? { parallelGroup: t.parallelGroup } : {}),
                ...(t.dependsOn ? { dependsOn: t.dependsOn } : {}),
              })),
            },
          });
        }
        const output: TaskBoardOutput = { tasks, applied };
        if (lastIterationRead !== undefined) {
          output.iterationState = lastIterationRead;
        }
        return {
          status: "ok",
          output,
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
