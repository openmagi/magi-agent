import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeRepoTaskStateTool } from "./RepoTaskState.js";

function toolContext(workspaceRoot: string): ToolContext {
  return {
    botId: "bot",
    sessionKey: "session",
    turnId: "turn-1",
    workspaceRoot,
    askUser: async () => ({}),
    emitProgress: () => {},
    abortSignal: new AbortController().signal,
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

describe("RepoTaskState", () => {
  let workspaceRoot: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "repo-task-state-"));
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("persists structured coding task state under .magi", async () => {
    const tool = makeRepoTaskStateTool(workspaceRoot);

    const updated = await tool.execute(
      {
        action: "update",
        goal: "fix login bug",
        plan: ["read code", "patch test"],
        touchedFiles: ["src/login.ts"],
        acceptanceCriteria: ["login test passes"],
      },
      toolContext(workspaceRoot),
    );
    const read = await tool.execute({ action: "read" }, toolContext(workspaceRoot));

    expect(updated.status).toBe("ok");
    expect(read.output?.state).toMatchObject({
      goal: "fix login bug",
      plan: ["read code", "patch test"],
      touchedFiles: ["src/login.ts"],
      acceptanceCriteria: ["login test passes"],
    });
    await expect(
      fs.stat(path.join(workspaceRoot, ".magi/repo-task-state.json")),
    ).resolves.toBeTruthy();
  });
});
