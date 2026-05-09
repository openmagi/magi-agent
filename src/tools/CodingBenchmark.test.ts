import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeCodingBenchmarkTool } from "./CodingBenchmark.js";

function makeCtx(workspaceRoot: string): ToolContext {
  return {
    botId: "bot-test",
    sessionKey: "agent:main:test:1",
    turnId: "turn-1",
    workspaceRoot,
    abortSignal: new AbortController().signal,
    askUser: async () => {
      throw new Error("askUser unavailable");
    },
    emitProgress: () => {},
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

describe("CodingBenchmark", () => {
  let root: string;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "coding-benchmark-"));
  });

  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("records benchmark outcomes and returns aggregate metrics", async () => {
    const tool = makeCodingBenchmarkTool(root);
    const ctx = makeCtx(root);

    await tool.execute(
      {
        action: "record",
        taskId: "bugfix-1",
        category: "bugfix",
        outcome: "passed",
        testsPassed: true,
        retryCount: 1,
        wrongCompletionClaims: 0,
        filesChanged: ["src/a.ts"],
      },
      ctx,
    );
    const result = await tool.execute(
      {
        action: "record",
        taskId: "ui-1",
        category: "ui",
        outcome: "failed",
        testsPassed: false,
        retryCount: 3,
        wrongCompletionClaims: 2,
        filesChanged: ["src/ui.tsx"],
      },
      ctx,
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      summary: {
        totalRuns: 2,
        passedRuns: 1,
        failedRuns: 1,
        successRate: 0.5,
        averageRetryCount: 2,
        wrongCompletionClaimRate: 1,
      },
    });

    const summary = await tool.execute({ action: "summary" }, ctx);
    expect(summary.output?.records).toHaveLength(2);
    expect(summary.metadata).toMatchObject({
      evidenceKind: "benchmark",
      totalRuns: 2,
      successRate: 0.5,
    });
  });

  it("lists deterministic golden coding tasks without creating a run workspace", async () => {
    const tool = makeCodingBenchmarkTool(root);
    const ctx = makeCtx(root);

    const result = await tool.execute({ action: "list_tasks" }, ctx);

    expect(result.status).toBe("ok");
    expect(result.output?.goldenTasks?.map((task) => task.id)).toEqual([
      "js-bugfix-arithmetic",
      "js-feature-clamp",
    ]);
    expect(result.output?.goldenTasks?.[0]).toMatchObject({
      category: "bugfix",
      verificationCommands: ["npm test"],
    });
    await expect(
      fs.access(path.join(root, ".magi", "coding-benchmark-golden")),
    ).rejects.toBeDefined();
  });

  it("starts a golden benchmark run by materializing task workspaces and a manifest", async () => {
    const tool = makeCodingBenchmarkTool(root);
    const ctx = makeCtx(root);

    const result = await tool.execute(
      {
        action: "start_run",
        suite: "coding-golden-v1",
        runId: "run-fixed",
        taskIds: ["js-bugfix-arithmetic"],
      },
      ctx,
    );

    expect(result.status).toBe("ok");
    expect(result.output?.goldenRun).toMatchObject({
      runId: "run-fixed",
      suite: "coding-golden-v1",
      taskCount: 1,
      tasks: [
        {
          id: "js-bugfix-arithmetic",
          workspacePath: ".magi/coding-benchmark-golden/run-fixed/js-bugfix-arithmetic/workspace",
          verificationCommands: ["npm test"],
        },
      ],
    });
    await expect(
      fs.readFile(
        path.join(
          root,
          ".magi/coding-benchmark-golden/run-fixed/js-bugfix-arithmetic/workspace/package.json",
        ),
        "utf8",
      ),
    ).resolves.toContain('"test"');
    await expect(
      fs.readFile(
        path.join(
          root,
          ".magi/coding-benchmark-golden/run-fixed/js-bugfix-arithmetic/workspace/src/math.js",
        ),
        "utf8",
      ),
    ).resolves.toContain("return a - b;");

    const manifestRaw = await fs.readFile(
      path.join(root, ".magi/coding-benchmark-golden/run-fixed/manifest.json"),
      "utf8",
    );
    const manifest = JSON.parse(manifestRaw) as {
      runId: string;
      suite: string;
      tasks: Array<{ id: string; workspacePath: string }>;
    };
    expect(manifest).toMatchObject({
      runId: "run-fixed",
      suite: "coding-golden-v1",
      tasks: [{ id: "js-bugfix-arithmetic" }],
    });
  });

  it("rejects unknown golden task ids before creating a run workspace", async () => {
    const tool = makeCodingBenchmarkTool(root);
    const ctx = makeCtx(root);

    const result = await tool.execute(
      {
        action: "start_run",
        suite: "coding-golden-v1",
        runId: "bad-run",
        taskIds: ["missing-task"],
      },
      ctx,
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("benchmark_failed");
    expect(result.errorMessage).toContain("unknown golden benchmark task: missing-task");
    await expect(
      fs.access(path.join(root, ".magi", "coding-benchmark-golden")),
    ).rejects.toBeDefined();
  });

  it("writes a benchmark report with task, category, and golden run summaries", async () => {
    const tool = makeCodingBenchmarkTool(root);
    const ctx = makeCtx(root);

    await tool.execute(
      {
        action: "start_run",
        suite: "coding-golden-v1",
        runId: "run-report",
        taskIds: ["js-bugfix-arithmetic", "js-feature-clamp"],
      },
      ctx,
    );
    await tool.execute(
      {
        action: "record",
        runId: "run-report",
        taskId: "js-bugfix-arithmetic",
        category: "bugfix",
        outcome: "passed",
        testsPassed: true,
        retryCount: 1,
        wrongCompletionClaims: 0,
      },
      ctx,
    );
    await tool.execute(
      {
        action: "record",
        runId: "run-report",
        taskId: "js-feature-clamp",
        category: "feature",
        outcome: "failed",
        testsPassed: false,
        retryCount: 2,
        wrongCompletionClaims: 1,
      },
      ctx,
    );

    const result = await tool.execute({ action: "report" }, ctx);

    expect(result.status).toBe("ok");
    expect(result.output?.report).toMatchObject({
      jsonPath: ".magi/coding-benchmark-reports/latest.json",
      markdownPath: ".magi/coding-benchmark-reports/latest.md",
      summary: {
        totalRuns: 2,
        passedRuns: 1,
        failedRuns: 1,
        successRate: 0.5,
      },
      byCategory: [
        {
          category: "bugfix",
          totalRuns: 1,
          passedRuns: 1,
          successRate: 1,
        },
        {
          category: "feature",
          totalRuns: 1,
          failedRuns: 1,
          successRate: 0,
        },
      ],
      byTask: [
        {
          taskId: "js-bugfix-arithmetic",
          runId: "run-report",
          totalRuns: 1,
          successRate: 1,
        },
        {
          taskId: "js-feature-clamp",
          runId: "run-report",
          totalRuns: 1,
          successRate: 0,
        },
      ],
      goldenRuns: [
        {
          runId: "run-report",
          taskCount: 2,
          recordedRuns: 2,
          successRate: 0.5,
        },
      ],
    });
    expect(result.metadata).toMatchObject({
      evidenceKind: "benchmark_report",
      reportPath: ".magi/coding-benchmark-reports/latest.json",
      markdownPath: ".magi/coding-benchmark-reports/latest.md",
      goldenRunCount: 1,
    });

    const reportJsonRaw = await fs.readFile(
      path.join(root, ".magi/coding-benchmark-reports/latest.json"),
      "utf8",
    );
    const reportJson = JSON.parse(reportJsonRaw) as {
      goldenRuns: Array<{ runId: string }>;
      byTask: Array<{ taskId: string }>;
    };
    expect(reportJson.goldenRuns).toEqual([
      {
        runId: "run-report",
        taskCount: 2,
        recordedRuns: 2,
        passedRuns: 1,
        failedRuns: 1,
        blockedRuns: 0,
        successRate: 0.5,
      },
    ]);
    expect(reportJson.byTask.map((task) => task.taskId)).toEqual([
      "js-bugfix-arithmetic",
      "js-feature-clamp",
    ]);

    const reportMarkdown = await fs.readFile(
      path.join(root, ".magi/coding-benchmark-reports/latest.md"),
      "utf8",
    );
    expect(reportMarkdown).toContain("# Coding Benchmark Report");
    expect(reportMarkdown).toContain("run-report");
    expect(reportMarkdown).toContain("js-feature-clamp");
  });
});
