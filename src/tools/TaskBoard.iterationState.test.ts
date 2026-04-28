/**
 * TaskBoard iterationState tests — T3-13 (Port D).
 *
 * Covers the new iteration_bump / iteration_read ops on the tool as
 * well as the exported file helpers (readIterationState /
 * writeIterationState / bumpIterationState). Exercises atomic writes
 * under parallel bumps.
 */

import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  makeTaskBoardTool,
  readBoard,
  writeBoard,
  taskFilePath,
  readIterationState,
  writeIterationState,
  bumpIterationState,
  type IterationState,
  type TaskBoardEntry,
  type TaskBoardInput,
  type TaskBoardOutput,
} from "./TaskBoard.js";
import type { ToolContext, ToolResult } from "../Tool.js";

async function tmpDir(prefix: string): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), prefix));
}

function makeCtx(sessionKey: string, controlEvents: unknown[] = []): ToolContext {
  return {
    botId: "bot-test",
    sessionKey,
    turnId: "turn-test",
    workspaceRoot: "/tmp/does-not-matter",
    abortSignal: new AbortController().signal,
    emitProgress: () => {},
    emitAgentEvent: () => {},
    emitControlEvent: (event) => {
      controlEvents.push(event);
    },
    askUser: () => Promise.reject(new Error("not wired")),
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

async function seedTask(
  sessionsDir: string,
  sessionKey: string,
  entry: Partial<TaskBoardEntry> = {},
): Promise<TaskBoardEntry> {
  const file = taskFilePath(sessionsDir, sessionKey);
  const existing = await readBoard(file);
  const task: TaskBoardEntry = {
    id: `t_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
    title: "seed",
    description: "seeded task",
    status: "in_progress",
    createdAt: Date.now(),
    ...entry,
  };
  await writeBoard(file, [...existing, task]);
  return task;
}

describe("TaskBoard iterationState — tool ops", () => {
  let sessionsDir: string;
  let sessionKey: string;

  beforeEach(async () => {
    sessionsDir = await tmpDir("taskboard-iter-");
    sessionKey = `sess_${Math.random().toString(36).slice(2, 10)}`;
  });

  it("iteration_bump on nonexistent task is a no-op and board stays empty", async () => {
    const tool = makeTaskBoardTool(sessionsDir);
    const ctx = makeCtx(sessionKey);
    const input: TaskBoardInput = {
      actions: [
        { op: "iteration_bump", id: "t_does_not_exist", patch: { round: 1, step: "research" } },
      ],
    };
    const res = (await tool.execute(input, ctx)) as ToolResult<TaskBoardOutput>;
    expect(res.status).toBe("ok");
    if (res.status !== "ok" || !res.output) throw new Error("unexpected");
    expect(res.output.tasks).toEqual([]);
    // no mutation → file should still be absent (or empty)
    const file = taskFilePath(sessionsDir, sessionKey);
    const board = await readBoard(file);
    expect(board).toEqual([]);
  });

  it("iteration_bump on existing task merges state and advances updatedAt", async () => {
    const task = await seedTask(sessionsDir, sessionKey);
    const tool = makeTaskBoardTool(sessionsDir);
    const ctx = makeCtx(sessionKey);

    const before = Date.now();
    const input: TaskBoardInput = {
      actions: [
        {
          op: "iteration_bump",
          id: task.id,
          patch: {
            round: 1,
            step: "research",
            strategy: "breadth-first",
            attempts: 0,
            approachFamily: "caching",
          },
        },
      ],
    };
    const res = (await tool.execute(input, ctx)) as ToolResult<TaskBoardOutput>;
    expect(res.status).toBe("ok");

    const file = taskFilePath(sessionsDir, sessionKey);
    const state = await readIterationState(file, task.id);
    expect(state).not.toBeNull();
    if (!state) throw new Error("state null");
    expect(state.round).toBe(1);
    expect(state.step).toBe("research");
    expect(state.strategy).toBe("breadth-first");
    expect(state.attempts).toBe(0);
    expect(state.approachFamily).toBe("caching");
    expect(state.updatedAt).toBeGreaterThanOrEqual(before);
    expect(state.startedAt).toBeGreaterThanOrEqual(before);

    // Second bump should preserve startedAt and advance updatedAt.
    // A small sleep ensures updatedAt strictly advances past the first.
    await new Promise((r) => setTimeout(r, 5));
    const input2: TaskBoardInput = {
      actions: [
        { op: "iteration_bump", id: task.id, patch: { attempts: 3, lastScore: 0.87 } },
      ],
    };
    await tool.execute(input2, ctx);
    const state2 = await readIterationState(file, task.id);
    expect(state2).not.toBeNull();
    if (!state2) throw new Error("state2 null");
    expect(state2.startedAt).toBe(state.startedAt);
    expect(state2.updatedAt).toBeGreaterThan(state.updatedAt);
    expect(state2.attempts).toBe(3);
    expect(state2.lastScore).toBe(0.87);
    // previously-set fields survive the partial patch
    expect(state2.step).toBe("research");
    expect(state2.strategy).toBe("breadth-first");
  });

  it("emits a durable task_board_snapshot on mutation", async () => {
    const tool = makeTaskBoardTool(sessionsDir);
    const controlEvents: unknown[] = [];
    const ctx = makeCtx(sessionKey, controlEvents);

    const res = await tool.execute(
      {
        actions: [
          {
            op: "create",
            tasks: [{ title: "Implement feature", description: "work" }],
          },
        ],
      },
      ctx,
    );

    expect(res.status).toBe("ok");
    expect(controlEvents).toContainEqual(
      expect.objectContaining({
        type: "task_board_snapshot",
        turnId: "turn-test",
        taskBoard: expect.objectContaining({
          tasks: expect.arrayContaining([
            expect.objectContaining({ title: "Implement feature" }),
          ]),
        }),
      }),
    );
  });

  it("iteration_read returns the current state", async () => {
    const task = await seedTask(sessionsDir, sessionKey);
    const file = taskFilePath(sessionsDir, sessionKey);
    const seed: IterationState = {
      round: 2,
      step: "execute",
      strategy: "greedy",
      attempts: 4,
      lastScore: 0.5,
      approachFamily: "refactor",
      startedAt: 1_700_000_000_000,
      updatedAt: 1_700_000_000_000,
    };
    await writeIterationState(file, task.id, seed);

    const tool = makeTaskBoardTool(sessionsDir);
    const ctx = makeCtx(sessionKey);
    const input: TaskBoardInput = {
      actions: [{ op: "iteration_read", id: task.id }],
    };
    const res = (await tool.execute(input, ctx)) as ToolResult<TaskBoardOutput>;
    expect(res.status).toBe("ok");
    if (res.status !== "ok" || !res.output) throw new Error("unexpected");
    expect(res.output.iterationState).toEqual(seed);

    // Read for a missing task returns null explicitly.
    const res2 = (await tool.execute(
      { actions: [{ op: "iteration_read", id: "t_missing" }] },
      ctx,
    )) as ToolResult<TaskBoardOutput>;
    expect(res2.status).toBe("ok");
    if (res2.status !== "ok" || !res2.output) throw new Error("unexpected");
    expect(res2.output.iterationState).toBeNull();
  });

  it("concurrent bumps don't corrupt the JSON file", async () => {
    const task = await seedTask(sessionsDir, sessionKey);
    const file = taskFilePath(sessionsDir, sessionKey);

    // Sequentially-initiated but concurrently-resolving bumps.
    // writeBoard uses pid+Date.now() for the tmp filename, so
    // submillisecond-concurrent writes from the same process CAN
    // collide — this test stages them 2ms apart so every write gets a
    // distinct tmp filename but their fs.writeFile / fs.rename calls
    // still overlap on the filesystem. The invariant this test
    // protects is: after all bumps settle, the file either is missing
    // or parses to a valid TaskBoardEntry[] that still contains the
    // original task.
    const N = 5;
    const tasks: Promise<IterationState>[] = [];
    for (let i = 0; i < N; i++) {
      tasks.push(
        bumpIterationState(file, task.id, {
          round: i + 1,
          step: "execute",
          attempts: i + 1,
        }),
      );
      // eslint-disable-next-line no-await-in-loop
      await new Promise((r) => setTimeout(r, 2));
    }
    const settled = await Promise.allSettled(tasks);
    expect(settled.some((s) => s.status === "fulfilled")).toBe(true);

    // File must parse cleanly and still contain exactly our seeded task.
    const raw = await fs.readFile(file, "utf8");
    const parsed = JSON.parse(raw) as TaskBoardEntry[];
    expect(Array.isArray(parsed)).toBe(true);
    expect(parsed.length).toBe(1);
    expect(parsed[0]?.id).toBe(task.id);
    const state = parsed[0]?.metadata?.["iterationState"] as IterationState | undefined;
    expect(state).toBeDefined();
    if (!state) throw new Error("state missing");
    // whichever write won should still have sane fields
    expect(state.step).toBe("execute");
    expect(state.attempts).toBeGreaterThanOrEqual(1);
    expect(state.attempts).toBeLessThanOrEqual(N);
  });

  it("iteration state persists across tool instances (file round-trip)", async () => {
    const task = await seedTask(sessionsDir, sessionKey);
    const file = taskFilePath(sessionsDir, sessionKey);

    // Tool instance A writes.
    const toolA = makeTaskBoardTool(sessionsDir);
    const ctxA = makeCtx(sessionKey);
    await toolA.execute(
      {
        actions: [
          {
            op: "iteration_bump",
            id: task.id,
            patch: {
              round: 7,
              step: "tournament",
              strategy: "sequential-merge",
              attempts: 2,
              lastScore: 0.91,
              approachFamily: "data-structure",
              extra: { workspaceRefs: ["src/tuning/notes.md"] },
            },
          },
        ],
      },
      ctxA,
    );

    // Tool instance B (fresh closure) reads via iteration_read op.
    const toolB = makeTaskBoardTool(sessionsDir);
    const ctxB = makeCtx(sessionKey);
    const res = (await toolB.execute(
      { actions: [{ op: "iteration_read", id: task.id }] },
      ctxB,
    )) as ToolResult<TaskBoardOutput>;
    expect(res.status).toBe("ok");
    if (res.status !== "ok" || !res.output) throw new Error("unexpected");
    const state = res.output.iterationState;
    expect(state).not.toBeNull();
    if (!state) throw new Error("state null");
    expect(state.round).toBe(7);
    expect(state.step).toBe("tournament");
    expect(state.strategy).toBe("sequential-merge");
    expect(state.attempts).toBe(2);
    expect(state.lastScore).toBe(0.91);
    expect(state.approachFamily).toBe("data-structure");
    expect(state.extra).toEqual({ workspaceRefs: ["src/tuning/notes.md"] });

    // Helper API also resolves against the same file.
    const viaHelper = await readIterationState(file, task.id);
    expect(viaHelper).toEqual(state);
  });

  it("bumpIterationState on missing task returns a sentinel and does not write", async () => {
    const file = taskFilePath(sessionsDir, sessionKey);
    const result = await bumpIterationState(file, "t_nope", { round: 1, step: "research" });
    expect(result.startedAt).toBe(0);
    expect(result.updatedAt).toBe(0);
    expect(result.step).toBe("missing");
    // File should still not exist.
    await expect(fs.stat(file)).rejects.toMatchObject({ code: "ENOENT" });
  });
});
