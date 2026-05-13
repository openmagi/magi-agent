import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeGitDiffTool } from "./GitDiff.js";

const execFileAsync = promisify(execFile);

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

describe("GitDiff", () => {
  let workspaceRoot: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "git-diff-"));
    await execFileAsync("git", ["init", "-q"], { cwd: workspaceRoot });
    await fs.writeFile(path.join(workspaceRoot, "app.ts"), "export const x = 1;\n", "utf8");
    await execFileAsync("git", ["add", "app.ts"], { cwd: workspaceRoot });
    await execFileAsync("git", ["commit", "-m", "init", "-q"], {
      cwd: workspaceRoot,
      env: {
        ...process.env,
        GIT_AUTHOR_NAME: "Test",
        GIT_AUTHOR_EMAIL: "test@example.com",
        GIT_COMMITTER_NAME: "Test",
        GIT_COMMITTER_EMAIL: "test@example.com",
      },
    });
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("returns structured diff evidence for changed tracked and untracked files", async () => {
    await fs.writeFile(path.join(workspaceRoot, "app.ts"), "export const x = 2;\n", "utf8");
    await fs.writeFile(path.join(workspaceRoot, "new.ts"), "export const y = 1;\n", "utf8");
    const tool = makeGitDiffTool(workspaceRoot);

    const out = await tool.execute({}, toolContext(workspaceRoot));

    expect(out.status).toBe("ok");
    expect(out.output?.changedFiles.sort()).toEqual(["app.ts", "new.ts"]);
    expect(out.output?.diff).toContain("-export const x = 1;");
    expect(out.output?.diff).toContain("+export const x = 2;");
    expect(out.metadata).toMatchObject({ evidenceKind: "diff" });
  });
});
