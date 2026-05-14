import { describe, expect, it } from "vitest";
import type { Discipline } from "../../Session.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { HookContext } from "../types.js";
import { makeCodingChildReviewGateHook } from "./codingChildReviewGate.js";

const CODING_DISCIPLINE: Discipline = {
  tdd: true,
  git: true,
  requireCommit: "hard",
  sourcePatterns: ["**/*.{ts,tsx,js,jsx}"],
  testPatterns: ["**/*.{test,spec}.{ts,tsx,js,jsx}"],
  maxChangesBeforeCommit: 5,
  skipTdd: false,
  lastClassifiedMode: "coding",
};

function hookContext(events: unknown[] = []): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey: "session",
    turnId: "turn-1",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: (event) => events.push(event),
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5000,
  };
}

function spawnAgentTranscript(input: {
  ts: number;
  toolUseId: string;
  persona: string;
  prompt?: string;
  writeSet?: string[];
  workspacePolicy?: string;
  finalText?: string;
  toolCallCount?: number;
}): TranscriptEntry[] {
  return [
    {
      kind: "tool_call",
      ts: input.ts,
      turnId: "turn-1",
      toolUseId: input.toolUseId,
      name: "SpawnAgent",
      input: {
        persona: input.persona,
        prompt: input.prompt ?? "work on src/app.ts",
        deliver: "return",
        ...(input.writeSet ? { write_set: input.writeSet } : {}),
        ...(input.workspacePolicy ? { workspace_policy: input.workspacePolicy } : {}),
      },
    },
    {
      kind: "tool_result",
      ts: input.ts + 1,
      turnId: "turn-1",
      toolUseId: input.toolUseId,
      status: "ok",
      output: JSON.stringify({
        taskId: input.toolUseId,
        status: "ok",
        finalText: input.finalText ?? "child completed",
        toolCallCount: input.toolCallCount ?? 2,
      }),
      isError: false,
    },
  ];
}

function spawnWorktreeApplyTranscript(ts: number): TranscriptEntry[] {
  return [
    {
      kind: "tool_call",
      ts,
      turnId: "turn-1",
      toolUseId: "apply-1",
      name: "SpawnWorktreeApply",
      input: { action: "cherry_pick", spawnDir: ".spawn/child" },
    },
    {
      kind: "tool_result",
      ts: ts + 1,
      turnId: "turn-1",
      toolUseId: "apply-1",
      status: "ok",
      output: JSON.stringify({
        action: "cherry_pick",
        applied: true,
        changedFiles: ["src/app.ts"],
      }),
      isError: false,
    },
  ];
}

function spawnWorktreeConflictTranscript(input: {
  ts: number;
  toolUseId: string;
  spawnDir?: string;
  conflictedFiles?: string[];
}): TranscriptEntry[] {
  const spawnDir = input.spawnDir ?? ".spawn/child";
  const conflictedFiles = input.conflictedFiles ?? ["src/app.ts"];
  return [
    {
      kind: "tool_call",
      ts: input.ts,
      turnId: "turn-1",
      toolUseId: input.toolUseId,
      name: "SpawnWorktreeApply",
      input: { action: "cherry_pick", spawnDir },
    },
    {
      kind: "tool_result",
      ts: input.ts + 1,
      turnId: "turn-1",
      toolUseId: input.toolUseId,
      status: "error",
      output: JSON.stringify({
        action: "cherry_pick",
        applied: false,
        spawnDir,
        conflictedFiles,
        conflictReview: {
          conflictKind: "cherry_pick",
          conflictedFiles,
          preservesChildWorktree: true,
          resolverSpawn: {
            persona: "conflict_resolver",
            prompt: "resolve conflict",
            deliver: "return",
            workspace_policy: "trusted",
            write_set: conflictedFiles,
          },
        },
      }),
      isError: true,
    },
  ];
}

function spawnWorktreeDispositionTranscript(input: {
  ts: number;
  toolUseId: string;
  action: "apply" | "cherry_pick" | "reject";
  spawnDir?: string;
  applied?: boolean;
}): TranscriptEntry[] {
  const spawnDir = input.spawnDir ?? ".spawn/child";
  const applied = input.applied ?? input.action !== "reject";
  return [
    {
      kind: "tool_call",
      ts: input.ts,
      turnId: "turn-1",
      toolUseId: input.toolUseId,
      name: "SpawnWorktreeApply",
      input: { action: input.action, spawnDir },
    },
    {
      kind: "tool_result",
      ts: input.ts + 1,
      turnId: "turn-1",
      toolUseId: input.toolUseId,
      status: "ok",
      output: JSON.stringify({
        action: input.action,
        applied,
        spawnDir,
      }),
      isError: false,
    },
  ];
}

function fileEditTranscript(ts: number): TranscriptEntry[] {
  return [
    {
      kind: "tool_call",
      ts,
      turnId: "turn-1",
      toolUseId: "edit-1",
      name: "FileEdit",
      input: { path: "src/app.ts" },
    },
    {
      kind: "tool_result",
      ts: ts + 1,
      turnId: "turn-1",
      toolUseId: "edit-1",
      status: "ok",
      isError: false,
    },
  ];
}

describe("coding child review gate", () => {
  it("blocks a completion claim when a coding child changed files without a later reviewer child", async () => {
    const hook = makeCodingChildReviewGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => [
          ...spawnAgentTranscript({
            ts: 1,
            toolUseId: "impl-1",
            persona: "coder",
            writeSet: ["src/app.ts"],
            workspacePolicy: "git_worktree",
          }),
          ...spawnWorktreeApplyTranscript(3),
        ],
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Implemented and verified.",
        toolCallCount: 2,
        toolReadHappened: true,
        userMessage: "fix the app",
        retryCount: 0,
        filesChanged: ["src/app.ts"],
        toolNames: ["SpawnAgent", "SpawnWorktreeApply"],
      },
      hookContext(),
    );

    expect(out.action).toBe("block");
    expect(out.action === "block" ? out.reason : "").toContain("RETRY:CODING_CHILD_REVIEW_REQUIRED");
    expect(out.action === "block" ? out.reason : "").toContain("reviewer");
  });

  it("blocks a completion claim while a child worktree conflict has no later resolver child", async () => {
    const hook = makeCodingChildReviewGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => [
          ...spawnWorktreeConflictTranscript({
            ts: 1,
            toolUseId: "conflict-1",
            conflictedFiles: ["src/app.ts"],
          }),
        ],
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Resolved and verified.",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "adopt the child work",
        retryCount: 0,
        filesChanged: [],
        toolNames: ["SpawnWorktreeApply"],
      },
      hookContext(),
    );

    expect(out.action).toBe("block");
    expect(out.action === "block" ? out.reason : "").toContain(
      "RETRY:SPAWN_WORKTREE_CONFLICT_RESOLUTION_REQUIRED",
    );
    expect(out.action === "block" ? out.reason : "").toContain("conflict_resolver");
    expect(out.action === "block" ? out.reason : "").toContain("src/app.ts");
  });

  it("allows a completion claim after same-spawn reject discards a child worktree conflict", async () => {
    const hook = makeCodingChildReviewGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => [
          ...spawnWorktreeConflictTranscript({
            ts: 1,
            toolUseId: "conflict-1",
            spawnDir: ".spawn/child",
            conflictedFiles: ["src/app.ts"],
          }),
          ...spawnWorktreeDispositionTranscript({
            ts: 3,
            toolUseId: "reject-1",
            action: "reject",
            spawnDir: ".spawn/child",
            applied: false,
          }),
        ],
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Resolved by rejecting the conflicted child work.",
        toolCallCount: 2,
        toolReadHappened: true,
        userMessage: "reject the child worktree conflict",
        retryCount: 0,
        filesChanged: [],
        toolNames: ["SpawnWorktreeApply", "SpawnWorktreeApply"],
      },
      hookContext(),
    );

    expect(out).toEqual({ action: "continue" });
  });

  it("keeps blocking when a child worktree conflict disposition targets a different spawnDir", async () => {
    const hook = makeCodingChildReviewGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => [
          ...spawnWorktreeConflictTranscript({
            ts: 1,
            toolUseId: "conflict-1",
            spawnDir: ".spawn/child-a",
            conflictedFiles: ["src/app.ts"],
          }),
          ...spawnWorktreeDispositionTranscript({
            ts: 3,
            toolUseId: "reject-1",
            action: "reject",
            spawnDir: ".spawn/child-b",
            applied: false,
          }),
        ],
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Resolved by rejecting the conflicted child work.",
        toolCallCount: 2,
        toolReadHappened: true,
        userMessage: "reject the child worktree conflict",
        retryCount: 0,
        filesChanged: [],
        toolNames: ["SpawnWorktreeApply", "SpawnWorktreeApply"],
      },
      hookContext(),
    );

    expect(out.action).toBe("block");
    expect(out.action === "block" ? out.reason : "").toContain(
      "RETRY:SPAWN_WORKTREE_CONFLICT_RESOLUTION_REQUIRED",
    );
  });

  it("moves past the conflict gate after same-spawn apply, then requires reviewer review", async () => {
    const hook = makeCodingChildReviewGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => [
          ...spawnAgentTranscript({
            ts: 1,
            toolUseId: "impl-1",
            persona: "coder",
            writeSet: ["src/app.ts"],
            workspacePolicy: "git_worktree",
          }),
          ...spawnWorktreeConflictTranscript({
            ts: 3,
            toolUseId: "conflict-1",
            spawnDir: ".spawn/child",
            conflictedFiles: ["src/app.ts"],
          }),
          ...spawnWorktreeDispositionTranscript({
            ts: 5,
            toolUseId: "apply-1",
            action: "apply",
            spawnDir: ".spawn/child",
            applied: true,
          }),
        ],
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Resolved and verified.",
        toolCallCount: 3,
        toolReadHappened: true,
        userMessage: "adopt the child worktree conflict",
        retryCount: 0,
        filesChanged: ["src/app.ts"],
        toolNames: ["SpawnAgent", "SpawnWorktreeApply", "SpawnWorktreeApply"],
      },
      hookContext(),
    );

    expect(out.action).toBe("block");
    expect(out.action === "block" ? out.reason : "").toContain("RETRY:CODING_CHILD_REVIEW_REQUIRED");
    expect(out.action === "block" ? out.reason : "").not.toContain(
      "SPAWN_WORKTREE_CONFLICT_RESOLUTION_REQUIRED",
    );
  });

  it("moves past the conflict gate after a matching conflict_resolver child, then requires reviewer review", async () => {
    const hook = makeCodingChildReviewGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => [
          ...spawnWorktreeConflictTranscript({
            ts: 1,
            toolUseId: "conflict-1",
            conflictedFiles: ["src/app.ts"],
          }),
          ...spawnAgentTranscript({
            ts: 3,
            toolUseId: "resolve-1",
            persona: "conflict_resolver",
            writeSet: ["src/app.ts"],
            workspacePolicy: "trusted",
            finalText: "Resolved src/app.ts and ran GitDiff/TestRun.",
            toolCallCount: 3,
          }),
        ],
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Resolved and verified.",
        toolCallCount: 4,
        toolReadHappened: true,
        userMessage: "adopt the child work",
        retryCount: 0,
        filesChanged: ["src/app.ts"],
        toolNames: ["SpawnWorktreeApply", "SpawnAgent"],
      },
      hookContext(),
    );

    expect(out.action).toBe("block");
    expect(out.action === "block" ? out.reason : "").toContain("RETRY:CODING_CHILD_REVIEW_REQUIRED");
    expect(out.action === "block" ? out.reason : "").not.toContain(
      "SPAWN_WORKTREE_CONFLICT_RESOLUTION_REQUIRED",
    );
  });

  it("allows a coding child completion after a successful reviewer child runs after the latest apply", async () => {
    const events: unknown[] = [];
    const hook = makeCodingChildReviewGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => [
          ...spawnAgentTranscript({
            ts: 1,
            toolUseId: "impl-1",
            persona: "implementer",
            writeSet: ["src/app.ts"],
          }),
          ...spawnWorktreeApplyTranscript(3),
          ...spawnAgentTranscript({
            ts: 5,
            toolUseId: "review-1",
            persona: "reviewer",
            prompt: "review src/app.ts against acceptance criteria",
            finalText: "No blocking issues found.",
            toolCallCount: 3,
          }),
        ],
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Implemented and verified.",
        toolCallCount: 3,
        toolReadHappened: true,
        userMessage: "fix the app",
        retryCount: 0,
        filesChanged: ["src/app.ts"],
        toolNames: ["SpawnAgent", "SpawnWorktreeApply", "SpawnAgent"],
      },
      hookContext(events),
    );

    expect(out).toEqual({ action: "continue" });
    expect(events).toContainEqual(expect.objectContaining({
      ruleId: "coding-child-review-gate",
      verdict: "ok",
    }));
  });

  it("treats a reviewer child before the latest apply as stale", async () => {
    const hook = makeCodingChildReviewGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => [
          ...spawnAgentTranscript({
            ts: 1,
            toolUseId: "impl-1",
            persona: "coder",
            writeSet: ["src/app.ts"],
          }),
          ...spawnAgentTranscript({
            ts: 3,
            toolUseId: "review-1",
            persona: "reviewer",
            finalText: "Looks ok before apply.",
          }),
          ...spawnWorktreeApplyTranscript(5),
        ],
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Implemented and verified.",
        toolCallCount: 3,
        toolReadHappened: true,
        userMessage: "fix the app",
        retryCount: 0,
        filesChanged: ["src/app.ts"],
        toolNames: ["SpawnAgent", "SpawnAgent", "SpawnWorktreeApply"],
      },
      hookContext(),
    );

    expect(out.action).toBe("block");
  });

  it("does not require a reviewer child for direct parent edits without coding child delegation", async () => {
    const hook = makeCodingChildReviewGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => fileEditTranscript(1),
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Implemented and verified.",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "fix the app",
        retryCount: 0,
        filesChanged: ["src/app.ts"],
        toolNames: ["FileEdit"],
      },
      hookContext(),
    );

    expect(out).toEqual({ action: "continue" });
  });

  it("does not treat a read-only coder consultation as child implementation work", async () => {
    const hook = makeCodingChildReviewGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => [
          ...spawnAgentTranscript({
            ts: 1,
            toolUseId: "consult-1",
            persona: "coder",
            prompt: "inspect the likely cause",
          }),
          ...fileEditTranscript(3),
        ],
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Implemented and verified.",
        toolCallCount: 2,
        toolReadHappened: true,
        userMessage: "fix the app",
        retryCount: 0,
        filesChanged: ["src/app.ts"],
        toolNames: ["SpawnAgent", "FileEdit"],
      },
      hookContext(),
    );

    expect(out).toEqual({ action: "continue" });
  });
});
