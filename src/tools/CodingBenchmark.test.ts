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
});
