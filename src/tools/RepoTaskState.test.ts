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

  it("tracks commit units and coding workspace lock lifecycle", async () => {
    const tool = makeRepoTaskStateTool(workspaceRoot);

    const acquired = await tool.execute(
      {
        action: "update",
        goal: "fix webhook idempotency",
        commitUnits: [
          {
            id: "unit-webhook",
            title: "Make webhook event handling idempotent",
            status: "in_progress",
            acceptanceCriteria: ["duplicate events notify once"],
            verificationCommands: ["npm test -- webhookHandler.test.js"],
          },
        ],
        activeUnitId: "unit-webhook",
        workspaceLock: {
          action: "acquire",
          goal: "fix webhook idempotency",
          activeUnitId: "unit-webhook",
        },
      },
      toolContext(workspaceRoot),
    );

    expect(acquired.status).toBe("ok");
    expect(acquired.output?.state.workspaceLock).toMatchObject({
      status: "active",
      ownerSessionKey: "session",
      goal: "fix webhook idempotency",
      activeUnitId: "unit-webhook",
    });
    expect(acquired.output?.state.commitUnits).toEqual([
      expect.objectContaining({
        id: "unit-webhook",
        status: "in_progress",
        verificationCommands: ["npm test -- webhookHandler.test.js"],
      }),
    ]);
    expect(acquired.output?.ledgerPath).toBe(".magi/coding/unit-webhook/log.md");

    const acquiredLedger = await fs.readFile(
      path.join(workspaceRoot, ".magi/coding/unit-webhook/log.md"),
      "utf8",
    );
    expect(acquiredLedger).toContain("# Coding Task Ledger");
    expect(acquiredLedger).toContain("Goal: fix webhook idempotency");
    expect(acquiredLedger).toContain("Workspace lock: active");
    expect(acquiredLedger).toContain("Owner session: session");
    expect(acquiredLedger).toContain("Active unit: unit-webhook");
    expect(acquiredLedger).toContain("### unit-webhook");
    expect(acquiredLedger).toContain("Title: Make webhook event handling idempotent");
    expect(acquiredLedger).toContain("Status: in_progress");
    expect(acquiredLedger).toContain("duplicate events notify once");
    expect(acquiredLedger).toContain("`npm test -- webhookHandler.test.js`");
    expect(acquiredLedger).toContain("unit unit-webhook started");

    const released = await tool.execute(
      {
        action: "update",
        commitUnits: [
          {
            id: "unit-webhook",
            status: "completed",
            changedFiles: ["src/webhookHandler.js"],
            commitSha: "abc123",
          },
        ],
        workspaceLock: {
          action: "release",
          reason: "unit completed",
        },
      },
      toolContext(workspaceRoot),
    );

    expect(released.status).toBe("ok");
    expect(released.output?.state.workspaceLock).toMatchObject({
      status: "released",
      ownerSessionKey: "session",
      releaseReason: "unit completed",
    });
    expect(released.output?.state.commitUnits[0]).toMatchObject({
      id: "unit-webhook",
      title: "Make webhook event handling idempotent",
      status: "completed",
      changedFiles: ["src/webhookHandler.js"],
      verificationCommands: ["npm test -- webhookHandler.test.js"],
      commitSha: "abc123",
    });

    expect(released.output?.ledgerPath).toBe(".magi/coding/unit-webhook/log.md");
    const releasedLedger = await fs.readFile(
      path.join(workspaceRoot, ".magi/coding/unit-webhook/log.md"),
      "utf8",
    );
    expect(releasedLedger).toContain("Workspace lock: released");
    expect(releasedLedger).toContain("Release reason: unit completed");
    expect(releasedLedger).toContain("Status: completed");
    expect(releasedLedger).toContain("src/webhookHandler.js");
    expect(releasedLedger).toContain("Commit: abc123");
    expect(releasedLedger).toContain("unit unit-webhook completed");
    expect(releasedLedger).toContain("lock released: unit completed");
  });

  it("sanitizes coding ledger paths derived from commit unit ids", async () => {
    const tool = makeRepoTaskStateTool(workspaceRoot);

    const updated = await tool.execute(
      {
        action: "update",
        goal: "safe ledger path",
        commitUnits: [
          {
            id: "../unit bad",
            title: "Unsafe unit id",
            status: "in_progress",
          },
        ],
        activeUnitId: "../unit bad",
        workspaceLock: {
          action: "acquire",
          activeUnitId: "../unit bad",
        },
      },
      toolContext(workspaceRoot),
    );

    expect(updated.status).toBe("ok");
    expect(updated.output?.ledgerPath).toBe(".magi/coding/unit-bad/log.md");
    await expect(
      fs.stat(path.join(workspaceRoot, ".magi/coding/unit-bad/log.md")),
    ).resolves.toBeTruthy();
    await expect(
      fs.stat(path.join(workspaceRoot, ".magi/unit bad/log.md")),
    ).rejects.toThrow();
  });

  it("refuses to acquire a workspace lock owned by another session", async () => {
    const tool = makeRepoTaskStateTool(workspaceRoot);
    await tool.execute(
      {
        action: "update",
        goal: "first task",
        workspaceLock: {
          action: "acquire",
          goal: "first task",
        },
      },
      toolContext(workspaceRoot),
    );

    const blocked = await tool.execute(
      {
        action: "update",
        goal: "second task",
        workspaceLock: {
          action: "acquire",
          goal: "second task",
        },
      },
      {
        ...toolContext(workspaceRoot),
        sessionKey: "other-session",
      },
    );

    expect(blocked).toMatchObject({
      status: "error",
      errorCode: "workspace_lock_active",
    });
    expect(blocked.output?.state.workspaceLock).toMatchObject({
      status: "active",
      ownerSessionKey: "session",
      goal: "first task",
    });
  });
});
