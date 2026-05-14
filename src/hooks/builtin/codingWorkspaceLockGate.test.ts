import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { Discipline } from "../../Session.js";
import type { HookContext } from "../types.js";
import { makeCodingWorkspaceLockHooks } from "./codingWorkspaceLockGate.js";

const CODING_DISCIPLINE: Discipline = {
  tdd: true,
  git: true,
  requireCommit: "hard",
  sourcePatterns: ["code/**/src/**/*.{ts,tsx,js,jsx}", "src/**/*.{ts,tsx,js,jsx}"],
  testPatterns: ["code/**/*.test.{ts,tsx,js,jsx}", "**/*.test.{ts,tsx,js,jsx}"],
  maxChangesBeforeCommit: 5,
  skipTdd: false,
  lastClassifiedMode: "coding",
};

function hookContext(workspaceRoot: string, sessionKey = "session-a"): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey,
    turnId: "turn-1",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5000,
  };
}

async function writeRepoTaskState(workspaceRoot: string, state: Record<string, unknown>): Promise<void> {
  const file = path.join(workspaceRoot, ".magi/repo-task-state.json");
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, `${JSON.stringify(state, null, 2)}\n`, "utf8");
}

describe("codingWorkspaceLockGate", () => {
  let workspaceRoot: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "coding-lock-gate-"));
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("blocks hard-mode coding file writes until RepoTaskState acquires a workspace lock", async () => {
    const hooks = makeCodingWorkspaceLockHooks({
      workspaceRoot,
      agent: { getSessionDiscipline: () => CODING_DISCIPLINE },
    });

    const out = await hooks.beforeToolUse.handler(
      { toolName: "FileWrite", toolUseId: "write-1", input: { path: "code/app/src/login.ts" } },
      hookContext(workspaceRoot),
    );

    expect(out).toMatchObject({ action: "block" });
    expect(out.action === "block" ? out.reason : "").toContain("RepoTaskState");
    expect(out.action === "block" ? out.reason : "").toContain("workspace lock");
  });

  it("blocks PatchApply coding mutations until the workspace lock is active", async () => {
    const hooks = makeCodingWorkspaceLockHooks({
      workspaceRoot,
      agent: { getSessionDiscipline: () => CODING_DISCIPLINE },
    });

    const out = await hooks.beforeToolUse.handler(
      {
        toolName: "PatchApply",
        toolUseId: "patch-1",
        input: {
          patch: [
            "--- a/code/app/src/login.ts",
            "+++ b/code/app/src/login.ts",
            "@@ -1 +1 @@",
            "-old",
            "+new",
          ].join("\n"),
        },
      },
      hookContext(workspaceRoot),
    );

    expect(out).toMatchObject({ action: "block" });
    expect(out.action === "block" ? out.reason : "").toContain("workspace lock");
  });

  it("allows coding file writes under a same-session active lock and in-progress unit", async () => {
    await writeRepoTaskState(workspaceRoot, {
      goal: "fix login",
      activeUnitId: "unit-login",
      commitUnits: [
        {
          id: "unit-login",
          title: "Fix login bug",
          status: "in_progress",
          updatedAt: "2026-05-10T00:00:00.000Z",
        },
      ],
      workspaceLock: {
        status: "active",
        lockId: "lock-1",
        ownerSessionKey: "session-a",
        goal: "fix login",
        activeUnitId: "unit-login",
        acquiredAt: "2026-05-10T00:00:00.000Z",
        updatedAt: "2026-05-10T00:00:00.000Z",
      },
    });
    const hooks = makeCodingWorkspaceLockHooks({
      workspaceRoot,
      agent: { getSessionDiscipline: () => CODING_DISCIPLINE },
    });

    const out = await hooks.beforeToolUse.handler(
      { toolName: "FileEdit", toolUseId: "edit-1", input: { path: "code/app/src/login.ts" } },
      hookContext(workspaceRoot),
    );

    expect(out).toEqual({ action: "continue" });
  });

  it("blocks coding writes when another session owns the active lock", async () => {
    await writeRepoTaskState(workspaceRoot, {
      workspaceLock: {
        status: "active",
        lockId: "lock-1",
        ownerSessionKey: "session-b",
        goal: "other task",
        acquiredAt: "2026-05-10T00:00:00.000Z",
        updatedAt: "2026-05-10T00:00:00.000Z",
      },
    });
    const hooks = makeCodingWorkspaceLockHooks({
      workspaceRoot,
      agent: { getSessionDiscipline: () => CODING_DISCIPLINE },
    });

    const out = await hooks.beforeToolUse.handler(
      { toolName: "FileWrite", toolUseId: "write-1", input: { path: "code/app/src/login.ts" } },
      hookContext(workspaceRoot),
    );

    expect(out).toMatchObject({ action: "block" });
    expect(out.action === "block" ? out.reason : "").toContain("session-b");
  });

  it("blocks completion claims while the active commit unit is still in progress", async () => {
    await writeRepoTaskState(workspaceRoot, {
      activeUnitId: "unit-login",
      commitUnits: [
        {
          id: "unit-login",
          title: "Fix login bug",
          status: "in_progress",
          updatedAt: "2026-05-10T00:00:00.000Z",
        },
      ],
      workspaceLock: {
        status: "active",
        lockId: "lock-1",
        ownerSessionKey: "session-a",
        goal: "fix login",
        activeUnitId: "unit-login",
        acquiredAt: "2026-05-10T00:00:00.000Z",
        updatedAt: "2026-05-10T00:00:00.000Z",
      },
    });
    const hooks = makeCodingWorkspaceLockHooks({
      workspaceRoot,
      agent: { getSessionDiscipline: () => CODING_DISCIPLINE },
    });

    const out = await hooks.beforeCommit.handler(
      {
        assistantText: "Implemented and completed.",
        toolCallCount: 3,
        toolReadHappened: true,
        userMessage: "fix login",
        retryCount: 0,
        filesChanged: ["code/app/src/login.ts"],
      },
      hookContext(workspaceRoot),
    );

    expect(out).toMatchObject({ action: "block" });
    expect(out.action === "block" ? out.reason : "").toContain("unit-login");
    expect(out.action === "block" ? out.reason : "").toContain("CommitCheckpoint");
  });

  it("allows completion claims after the active commit unit is completed", async () => {
    await writeRepoTaskState(workspaceRoot, {
      activeUnitId: "unit-login",
      commitUnits: [
        {
          id: "unit-login",
          title: "Fix login bug",
          status: "completed",
          commitSha: "abc123",
          updatedAt: "2026-05-10T00:00:00.000Z",
        },
      ],
      workspaceLock: {
        status: "active",
        lockId: "lock-1",
        ownerSessionKey: "session-a",
        goal: "fix login",
        activeUnitId: "unit-login",
        acquiredAt: "2026-05-10T00:00:00.000Z",
        updatedAt: "2026-05-10T00:00:00.000Z",
      },
    });
    const hooks = makeCodingWorkspaceLockHooks({
      workspaceRoot,
      agent: { getSessionDiscipline: () => CODING_DISCIPLINE },
    });

    const out = await hooks.beforeCommit.handler(
      {
        assistantText: "Implemented and completed.",
        toolCallCount: 3,
        toolReadHappened: true,
        userMessage: "fix login",
        retryCount: 0,
        filesChanged: ["code/app/src/login.ts"],
      },
      hookContext(workspaceRoot),
    );

    expect(out).toEqual({ action: "continue" });
  });
});
