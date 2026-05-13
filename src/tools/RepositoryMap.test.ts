import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeRepositoryMapTool } from "./RepositoryMap.js";

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

async function commitAll(workspaceRoot: string): Promise<void> {
  await execFileAsync("git", ["add", "."], { cwd: workspaceRoot });
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
}

describe("RepositoryMap", () => {
  let workspaceRoot: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "repository-map-"));
    await execFileAsync("git", ["init", "-q"], { cwd: workspaceRoot });
    await fs.mkdir(path.join(workspaceRoot, "src"), { recursive: true });
    await fs.mkdir(path.join(workspaceRoot, "packages/api/src"), { recursive: true });
    await fs.writeFile(
      path.join(workspaceRoot, "package.json"),
      JSON.stringify(
        {
          scripts: {
            test: "vitest run",
            lint: "eslint .",
            build: "tsc -p tsconfig.json",
          },
        },
        null,
        2,
      ),
    );
    await fs.writeFile(path.join(workspaceRoot, "package-lock.json"), "{}\n");
    await fs.writeFile(path.join(workspaceRoot, "tsconfig.json"), "{}\n");
    await fs.writeFile(path.join(workspaceRoot, "src/index.ts"), "export const x = 1;\n");
    await fs.writeFile(path.join(workspaceRoot, "src/index.test.ts"), "import './index';\n");
    await fs.writeFile(
      path.join(workspaceRoot, "packages/api/package.json"),
      JSON.stringify({ scripts: { test: "vitest run packages/api" } }, null, 2),
    );
    await fs.writeFile(path.join(workspaceRoot, "packages/api/src/server.ts"), "export {};\n");
    await commitAll(workspaceRoot);
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("summarizes project roots, important files, and the current diff", async () => {
    await fs.writeFile(path.join(workspaceRoot, "src/index.ts"), "export const x = 2;\n");
    await fs.writeFile(path.join(workspaceRoot, "src/new.ts"), "export const y = 1;\n");
    const tool = makeRepositoryMapTool(workspaceRoot);

    const result = await tool.execute({}, toolContext(workspaceRoot));

    expect(result.status).toBe("ok");
    expect(result.output?.cwd).toBe(".");
    expect(result.output?.projectRoots).toEqual([
      expect.objectContaining({
        path: ".",
        types: ["node", "typescript"],
        packageManager: "npm",
        scripts: {
          build: "tsc -p tsconfig.json",
          lint: "eslint .",
          test: "vitest run",
        },
        tsconfig: "tsconfig.json",
        sourceDirs: ["src"],
        testDirs: ["src"],
      }),
      expect.objectContaining({
        path: "packages/api",
        types: ["node"],
        scripts: {
          test: "vitest run packages/api",
        },
        sourceDirs: ["packages/api/src"],
      }),
    ]);
    expect(result.output?.files).toEqual(
      expect.arrayContaining([
        { path: "package.json", kind: "metadata" },
        { path: "src/index.ts", kind: "source" },
        { path: "src/index.test.ts", kind: "test" },
        { path: "packages/api/package.json", kind: "metadata" },
      ]),
    );
    expect(result.output?.currentDiff).toMatchObject({
      isGitRepo: true,
      changedFiles: ["src/index.ts", "src/new.ts"],
    });
    expect(result.metadata).toMatchObject({
      evidenceKind: "repository_map",
      projectRootCount: 2,
    });
  });
});
