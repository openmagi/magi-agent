import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { HookContext } from "../types.js";
import {
  makeTaskBoardCompletionGateHook,
  readActiveTaskBoardItems,
} from "./taskBoardCompletionGate.js";
import {
  taskFilePath,
  writeBoard,
  type TaskBoardEntry,
} from "../../tools/TaskBoard.js";

function makeCtx(sessionKey = "session-test", events: unknown[] = []): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey,
    turnId: "turn",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: (event) => {
      events.push(event);
    },
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 1_000,
  };
}

function task(
  id: string,
  status: TaskBoardEntry["status"],
): TaskBoardEntry {
  return {
    id,
    title: `Task ${id}`,
    description: "desc",
    status,
    createdAt: 1,
  };
}

describe("taskBoardCompletionGate", () => {
  let tmp: string;
  let sessionsDir: string;

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "task-board-gate-"));
    sessionsDir = path.join(tmp, "sessions");
    await fs.mkdir(sessionsDir, { recursive: true });
  });

  afterEach(async () => {
    await fs.rm(tmp, { recursive: true, force: true });
  });

  it("reads in-progress task board entries for a session", async () => {
    const file = taskFilePath(sessionsDir, "session-test");
    await writeBoard(file, [
      task("a", "completed"),
      task("b", "in_progress"),
      task("c", "pending"),
    ]);

    const active = await readActiveTaskBoardItems(sessionsDir, "session-test");
    expect(active.map((t) => t.id)).toEqual(["b"]);
  });

  it("blocks completion claims while task board has in-progress work", async () => {
    const file = taskFilePath(sessionsDir, "session-test");
    await writeBoard(file, [task("a", "in_progress")]);
    const hook = makeTaskBoardCompletionGateHook({ sessionsDir });

    const result = await hook.handler(
      {
        assistantText: "완료했습니다. 검증도 통과했습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "fix it",
        retryCount: 0,
      },
      makeCtx(),
    );

    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[RETRY:TASK_BOARD_FOLLOW_THROUGH]");
      expect(result.reason).toContain("Task a");
    }
  });

  it("allows completion claims when no tasks are in progress", async () => {
    const file = taskFilePath(sessionsDir, "session-test");
    await writeBoard(file, [task("a", "completed"), task("b", "pending")]);
    const hook = makeTaskBoardCompletionGateHook({ sessionsDir });

    const result = await hook.handler(
      {
        assistantText: "완료했습니다. 검증도 통과했습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "fix it",
        retryCount: 0,
      },
      makeCtx(),
    );

    expect(result?.action).toBe("continue");
  });

  it("blocks after three completed tasks without a verification task", async () => {
    const file = taskFilePath(sessionsDir, "session-test");
    await writeBoard(file, [
      task("a", "completed"),
      task("b", "completed"),
      task("c", "completed"),
    ]);
    const hook = makeTaskBoardCompletionGateHook({ sessionsDir });
    const events: unknown[] = [];

    const result = await hook.handler(
      {
        assistantText: "완료했습니다. 테스트도 통과했습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "finish it",
        retryCount: 0,
      },
      makeCtx("session-test", events),
    );

    expect(result?.action).toBe("block");
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "rule_check",
        ruleId: "task-board-verification-gate",
        verdict: "violation",
      }),
    );
  });

  it("allows three completed tasks when a verification task exists", async () => {
    const file = taskFilePath(sessionsDir, "session-test");
    await writeBoard(file, [
      task("a", "completed"),
      task("b", "completed"),
      task("c", "completed"),
      {
        ...task("verify", "completed"),
        title: "Run verification tests",
      },
    ]);
    const hook = makeTaskBoardCompletionGateHook({ sessionsDir });

    const result = await hook.handler(
      {
        assistantText: "완료했습니다. 테스트도 통과했습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "finish it",
        retryCount: 0,
      },
      makeCtx(),
    );

    expect(result?.action).toBe("continue");
  });
});
