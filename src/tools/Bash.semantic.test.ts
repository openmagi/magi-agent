import { describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { ToolContext } from "../Tool.js";
import { BackgroundTaskRegistry } from "../tasks/BackgroundTaskRegistry.js";
import { interpretBashExit, makeBashTool } from "./Bash.js";
import { makeTaskGetTool, type TaskGetOutput } from "./TaskGet.js";
import { makeTaskOutputTool, type TaskOutputOutput } from "./TaskOutput.js";
import { makeTaskStopTool, type TaskStopOutput } from "./TaskStop.js";

function makeCtx(root: string): ToolContext {
  return {
    botId: "bot-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    workspaceRoot: root,
    askUser: async () => ({ selectedId: "approve" }),
    emitProgress: () => {},
    abortSignal: new AbortController().signal,
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

async function sleep(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

describe("Bash semantic exit handling", () => {
  it("marks raw Bash as dangerous so permission mode asks before execution", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bash-semantic-"));
    try {
      const tool = makeBashTool(root);

      expect(tool.permission).toBe("execute");
      expect(tool.dangerous).toBe(true);
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("recognizes semantic no-match exits for path-qualified grep binaries", () => {
    expect(interpretBashExit("/usr/bin/grep needle haystack.txt", 1, "")).toMatchObject({
      status: "ok",
      semanticStatus: "no_match",
    });
  });

  it("treats grep no-match exit 1 as a semantic success", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bash-semantic-"));
    try {
      const tool = makeBashTool(root);
      const result = await tool.execute(
        { command: "printf 'alpha\\n' | grep -q beta" },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      expect(result.errorCode).toBeUndefined();
      expect(result.metadata).toMatchObject({
        semanticStatus: "no_match",
      });
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("treats diff exit 1 as a semantic success with differences", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bash-semantic-"));
    try {
      await fs.writeFile(path.join(root, "a.txt"), "alpha\n", "utf8");
      await fs.writeFile(path.join(root, "b.txt"), "beta\n", "utf8");
      const tool = makeBashTool(root);
      const result = await tool.execute(
        { command: "diff a.txt b.txt" },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      expect(result.errorCode).toBeUndefined();
      expect(result.metadata).toMatchObject({
        semanticStatus: "different",
      });
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("reports timed-out commands explicitly", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bash-semantic-"));
    try {
      const tool = makeBashTool(root);
      const result = await tool.execute(
        { command: "sleep 1", timeoutMs: 50 },
        makeCtx(root),
      );

      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("timeout");
      expect(result.errorMessage).toContain("timed out after 50ms");
      expect(result.output).toMatchObject({
        exitCode: null,
        timedOut: true,
      });
      expect(result.metadata).toMatchObject({
        semanticStatus: "failed",
        timedOut: true,
      });
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("returns full stdout and stderr log file paths when output is truncated", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bash-semantic-"));
    try {
      const tool = makeBashTool(root);
      const outputBytes = 530 * 1024;
      const result = await tool.execute(
        {
          command: `node -e "process.stdout.write('o'.repeat(${outputBytes}) + 'STDOUT_END'); process.stderr.write('e'.repeat(${outputBytes}) + 'STDERR_END')"`,
        },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      expect(result.output?.truncated).toBe(true);
      expect(result.output?.stdout).not.toContain("STDOUT_END");
      expect(result.output?.stderr).not.toContain("STDERR_END");
      expect(result.output?.stdoutFile).toMatch(
        /^\.openmagi\/command-logs\/turn-test\/Bash-\d+-stdout\.log$/,
      );
      expect(result.output?.stderrFile).toMatch(
        /^\.openmagi\/command-logs\/turn-test\/Bash-\d+-stderr\.log$/,
      );

      const stdoutFile = result.output?.stdoutFile;
      const stderrFile = result.output?.stderrFile;
      expect(stdoutFile).toBeTruthy();
      expect(stderrFile).toBeTruthy();
      const stdoutFull = await fs.readFile(path.join(root, stdoutFile!), "utf8");
      const stderrFull = await fs.readFile(path.join(root, stderrFile!), "utf8");
      expect(stdoutFull).toHaveLength(outputBytes + "STDOUT_END".length);
      expect(stderrFull).toHaveLength(outputBytes + "STDERR_END".length);
      expect(stdoutFull.endsWith("STDOUT_END")).toBe(true);
      expect(stderrFull.endsWith("STDERR_END")).toBe(true);
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("starts a background shell task and records final output for TaskOutput", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bash-semantic-"));
    try {
      const registry = new BackgroundTaskRegistry(root);
      const tool = makeBashTool(root, registry);
      const getTool = makeTaskGetTool(registry);
      const outputTool = makeTaskOutputTool(registry);
      const ctx = makeCtx(root);
      const result = await tool.execute(
        {
          command: "printf start; sleep 0.1; printf BG_DONE",
          runInBackground: true,
          timeoutMs: 2_000,
        },
        ctx,
      );

      expect(result.status).toBe("ok");
      expect(result.output?.backgroundTaskId).toMatch(/^shell_/);
      expect(result.output?.stdout).toBe("");
      expect(result.output?.stdoutFile).toMatch(
        /^\.openmagi\/command-logs\/turn-test\/Bash-\d+-stdout\.log$/,
      );

      const taskId = result.output?.backgroundTaskId;
      expect(taskId).toBeTruthy();
      const running = await getTool.execute({ taskId: taskId! }, ctx);
      expect(running.status).toBe("ok");
      expect((running.output as TaskGetOutput).status).toBe("running");

      await sleep(180);

      const output = await outputTool.execute({ taskId: taskId! }, ctx);
      expect(output.status).toBe("ok");
      const taskOutput = output.output as TaskOutputOutput;
      expect(taskOutput.status).toBe("completed");
      expect(taskOutput.resultText).toBeTruthy();
      const parsed = JSON.parse(taskOutput.resultText!);
      expect(parsed.exitCode).toBe(0);
      expect(parsed.stdout).toBe("startBG_DONE");
      expect(parsed.stdoutFile).toBe(result.output?.stdoutFile);
      const fullStdout = await fs.readFile(path.join(root, parsed.stdoutFile), "utf8");
      expect(fullStdout).toBe("startBG_DONE");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("stops a running background shell task through TaskStop", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bash-semantic-"));
    try {
      const registry = new BackgroundTaskRegistry(root);
      const tool = makeBashTool(root, registry);
      const stopTool = makeTaskStopTool(registry);
      const getTool = makeTaskGetTool(registry);
      const ctx = makeCtx(root);
      const result = await tool.execute(
        {
          command: "sleep 1",
          runInBackground: true,
          timeoutMs: 5_000,
        },
        ctx,
      );
      const taskId = result.output?.backgroundTaskId;
      expect(taskId).toBeTruthy();

      const stopped = await stopTool.execute(
        { taskId: taskId!, reason: "test stop" },
        ctx,
      );
      expect(stopped.status).toBe("ok");
      expect((stopped.output as TaskStopOutput).stopped).toBe(true);
      await sleep(40);

      const got = await getTool.execute({ taskId: taskId! }, ctx);
      expect(got.status).toBe("ok");
      const record = got.output as TaskGetOutput;
      expect(record.status).toBe("aborted");
      expect(record.error).toContain("test stop");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });
});
