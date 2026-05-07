import { describe, expect, it } from "vitest";
import type { Discipline } from "../../Session.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { HookContext } from "../types.js";
import { makeCodingVerificationGateHook } from "./codingVerificationGate.js";

const CODING_DISCIPLINE: Discipline = {
  tdd: true,
  git: true,
  requireCommit: "soft",
  sourcePatterns: ["**/*.{ts,tsx,js,jsx}"],
  testPatterns: ["**/*.{test,spec}.{ts,tsx,js,jsx}"],
  maxChangesBeforeCommit: 5,
  skipTdd: false,
  lastClassifiedMode: "coding",
};

function hookContext(): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey: "session",
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

function testRunTranscript(): TranscriptEntry[] {
  return [
    {
      kind: "tool_call",
      ts: 1,
      turnId: "turn-1",
      toolUseId: "test-1",
      name: "TestRun",
      input: { command: "npm test -- src/app.test.ts" },
    },
    {
      kind: "tool_result",
      ts: 2,
      turnId: "turn-1",
      toolUseId: "test-1",
      status: "ok",
      output: JSON.stringify({ passed: true, exitCode: 0 }),
      isError: false,
    },
  ];
}

describe("coding verification gate", () => {
  it("blocks coding completions after file changes when no verification evidence exists", async () => {
    const hook = makeCodingVerificationGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => [],
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Implemented and completed.",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "fix the bug",
        retryCount: 0,
        filesChanged: ["workspace/code/app/src/app.ts"],
      },
      hookContext(),
    );

    expect(out).toEqual({
      action: "block",
      reason: expect.stringContaining("TestRun"),
    });
  });

  it("allows coding completions when a current-turn TestRun passed", async () => {
    const hook = makeCodingVerificationGateHook({
      agent: {
        getSessionDiscipline: () => CODING_DISCIPLINE,
        readSessionTranscript: async () => testRunTranscript(),
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Implemented and verified.",
        toolCallCount: 2,
        toolReadHappened: true,
        userMessage: "fix the bug",
        retryCount: 0,
        filesChanged: ["workspace/code/app/src/app.ts"],
      },
      hookContext(),
    );

    expect(out).toEqual({ action: "continue" });
  });

  it("does not gate non-coding turns", async () => {
    const hook = makeCodingVerificationGateHook({
      agent: {
        getSessionDiscipline: () => ({ ...CODING_DISCIPLINE, lastClassifiedMode: "research" }),
        readSessionTranscript: async () => [],
      },
    });

    const out = await hook.handler(
      {
        assistantText: "Updated the note.",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "write a note",
        retryCount: 0,
        filesChanged: ["notes.md"],
      },
      hookContext(),
    );

    expect(out).toEqual({ action: "continue" });
  });
});
