import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { makeTaskWaitTool, type TaskWaitOutput } from "./TaskWait.js";
import { BackgroundTaskRegistry } from "../tasks/BackgroundTaskRegistry.js";
import type { ToolContext } from "../Tool.js";

function stubCtx(): ToolContext {
  return {
    botId: "test",
    sessionKey: "s1",
    turnId: "t1",
    workspaceRoot: "/tmp",
    abortSignal: new AbortController().signal,
    askUser: async () => ({}),
    emitProgress: () => {},
    staging: {
      stageFileWrite() {},
      stageTranscriptAppend() {},
      stageAuditEvent() {},
    },
  };
}

describe("TaskWait", () => {
  let tmpDir: string;
  let registry: BackgroundTaskRegistry;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "taskwait-"));
    registry = new BackgroundTaskRegistry(tmpDir);
  });

  afterEach(() => {
    registry.clearControllers();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("validates empty taskIds", () => {
    const tool = makeTaskWaitTool(registry);
    expect(tool.validate!({ taskIds: [] })).toBeTruthy();
  });

  it("validates too many taskIds", () => {
    const tool = makeTaskWaitTool(registry);
    expect(tool.validate!({ taskIds: Array(11).fill("a") })).toContain("exceed");
  });

  it("returns error for unknown taskIds", async () => {
    const tool = makeTaskWaitTool(registry);
    const result = await tool.execute({ taskIds: ["nonexistent"] }, stubCtx());
    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("not_found");
  });

  it("returns immediately for already-completed tasks", async () => {
    const taskId = await registry.create({ taskId: `task-${Math.random().toString(36).slice(2, 8)}`, parentTurnId: "t1", sessionKey: "s1", persona: "test", prompt: "test prompt" }).then(r => r.taskId);
    await registry.attachResult(taskId, { status: "completed", resultText: "done" });

    const tool = makeTaskWaitTool(registry);
    const result = await tool.execute({ taskIds: [taskId] }, stubCtx());
    expect(result.status).toBe("ok");
    const output = result.output as TaskWaitOutput;
    expect(output.results).toHaveLength(1);
    expect(output.results[0]!.status).toBe("completed");
    expect(output.results[0]!.resultText).toBe("done");
    expect(output.timedOut).toBe(false);
  });

  it("waits for running tasks to complete", async () => {
    const taskId = await registry.create({ taskId: `task-${Math.random().toString(36).slice(2, 8)}`, parentTurnId: "t1", sessionKey: "s1", persona: "test", prompt: "test prompt" }).then(r => r.taskId);

    const tool = makeTaskWaitTool(registry);
    const waitPromise = tool.execute(
      { taskIds: [taskId], timeout_ms: 5000 },
      stubCtx(),
    );

    setTimeout(() => registry.attachResult(taskId, { status: "completed", resultText: "finally done" }), 100);

    const result = await waitPromise;
    expect(result.status).toBe("ok");
    const output = result.output as TaskWaitOutput;
    expect(output.results[0]!.status).toBe("completed");
    expect(output.results[0]!.resultText).toBe("finally done");
  });

  it("waits for multiple tasks", async () => {
    const t1 = (await registry.create({ taskId: "t1-id", parentTurnId: "t1", sessionKey: "s1", persona: "test", prompt: "p1" })).taskId;
    const t2 = (await registry.create({ taskId: "t2-id", parentTurnId: "t1", sessionKey: "s1", persona: "test", prompt: "p2" })).taskId;

    const tool = makeTaskWaitTool(registry);
    const waitPromise = tool.execute(
      { taskIds: [t1, t2], timeout_ms: 5000 },
      stubCtx(),
    );

    setTimeout(() => registry.attachResult(t1, { status: "completed", resultText: "r1" }), 50);
    setTimeout(() => registry.attachResult(t2, { status: "completed", resultText: "r2" }), 100);

    const result = await waitPromise;
    expect(result.status).toBe("ok");
    const output = result.output as TaskWaitOutput;
    expect(output.results).toHaveLength(2);
    expect(output.results.every((r) => r.status === "completed")).toBe(true);
  });

  it("respects timeout", async () => {
    const taskId = await registry.create({ taskId: `task-timeout`, parentTurnId: "t1", sessionKey: "s1", persona: "test", prompt: "test prompt" }).then(r => r.taskId);

    const tool = makeTaskWaitTool(registry);
    const result = await tool.execute(
      { taskIds: [taskId], timeout_ms: 5000 },
      stubCtx(),
    );
    expect(result.status).toBe("ok");
    const output = result.output as TaskWaitOutput;
    expect(output.timedOut).toBe(true);
    expect(output.results[0]!.status).toBe("running");
  }, 10_000);
});
