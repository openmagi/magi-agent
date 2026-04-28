import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import {
  hasFreshCompletionEvidence,
  makeCompletionEvidenceGateHook,
  matchesCompletionClaim,
} from "./completionEvidenceGate.js";
import { DebugWorkflow } from "../../debug/DebugWorkflow.js";

function makeCtx(
  transcript: TranscriptEntry[] = [],
  debugWorkflow?: DebugWorkflow,
): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: {} as HookContext["llm"],
    transcript,
    emit: () => {},
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
    debugWorkflow,
  };
}

function args(assistantText: string, retryCount = 0) {
  return {
    assistantText,
    toolCallCount: 0,
    toolReadHappened: false,
    userMessage: "fix it",
    retryCount,
  };
}

describe("completionEvidenceGate helpers", () => {
  it("detects completion and pass claims", () => {
    expect(matchesCompletionClaim("수정했고 테스트도 통과했습니다.")).toBe(true);
    expect(matchesCompletionClaim("Fixed and the build passes.")).toBe(true);
  });

  it("does not treat explicit non-verification as success", () => {
    expect(matchesCompletionClaim("수정은 했지만 검증은 아직 못 했습니다.")).toBe(false);
    expect(matchesCompletionClaim("I changed it, but I did not run tests.")).toBe(false);
  });

  it("accepts same-turn successful verification command output", () => {
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-test",
        toolUseId: "tool-1",
        name: "Bash",
        input: { command: "npm test -- completionEvidenceGate.test.ts" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-test",
        toolUseId: "tool-1",
        status: "ok",
        output: "Tests 1 passed",
      },
    ];
    expect(hasFreshCompletionEvidence(transcript, "turn-test")).toBe(true);
  });

  it("rejects failed verification output as evidence", () => {
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-test",
        toolUseId: "tool-1",
        name: "Bash",
        input: { command: "npm test" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-test",
        toolUseId: "tool-1",
        status: "error",
        isError: true,
        output: "1 failed",
      },
    ];
    expect(hasFreshCompletionEvidence(transcript, "turn-test")).toBe(false);
  });

  it("does not accept successful file edits as verification evidence", () => {
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-test",
        toolUseId: "tool-1",
        name: "FileEdit",
        input: { path: "src/a.ts" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-test",
        toolUseId: "tool-1",
        status: "ok",
        output: "changed",
      },
    ];
    expect(hasFreshCompletionEvidence(transcript, "turn-test")).toBe(false);
  });
});

describe("completionEvidenceGate hook", () => {
  const originalEnv = process.env.CORE_AGENT_COMPLETION_EVIDENCE;

  beforeEach(() => {
    delete process.env.CORE_AGENT_COMPLETION_EVIDENCE;
  });

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.CORE_AGENT_COMPLETION_EVIDENCE;
    } else {
      process.env.CORE_AGENT_COMPLETION_EVIDENCE = originalEnv;
    }
  });

  it("blocks success claims without fresh evidence", async () => {
    const hook = makeCompletionEvidenceGateHook();
    const result = await hook.handler(args("수정 완료했고 테스트도 통과했습니다."), makeCtx());
    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[RETRY:COMPLETION_EVIDENCE]");
    }
  });

  it("continues when same-turn verification evidence exists", async () => {
    const hook = makeCompletionEvidenceGateHook();
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-test",
        toolUseId: "tool-1",
        name: "Bash",
        input: { command: "npm run lint && npm test" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-test",
        toolUseId: "tool-1",
        status: "ok",
        output: "passed",
      },
    ];
    const result = await hook.handler(args("수정했고 테스트도 통과했습니다."), makeCtx(transcript));
    expect(result).toEqual({ action: "continue" });
  });

  it("blocks fixed claims after file edits until verification runs", async () => {
    const hook = makeCompletionEvidenceGateHook();
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-test",
        toolUseId: "tool-1",
        name: "FileEdit",
        input: { path: "src/a.ts" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-test",
        toolUseId: "tool-1",
        status: "ok",
        output: "changed",
      },
    ];
    const result = await hook.handler(args("Fixed and tests pass."), makeCtx(transcript));
    expect(result?.action).toBe("block");
  });

  it("fails open after retry budget is exhausted", async () => {
    const hook = makeCompletionEvidenceGateHook();
    const result = await hook.handler(args("Fixed and tests pass.", 1), makeCtx());
    expect(result).toEqual({ action: "continue" });
  });

  it("respects CORE_AGENT_COMPLETION_EVIDENCE=off", async () => {
    process.env.CORE_AGENT_COMPLETION_EVIDENCE = "off";
    const hook = makeCompletionEvidenceGateHook();
    const result = await hook.handler(args("Fixed and tests pass."), makeCtx());
    expect(result).toEqual({ action: "continue" });
  });

  it("tightens fix claims on debug turns until investigation and verification both happened", async () => {
    const workflow = new DebugWorkflow();
    workflow.classifyTurn("session-test", "turn-test", "The regression is still failing");
    workflow.recordVerification("session-test", "turn-test", "npm test");
    const hook = makeCompletionEvidenceGateHook({ debugWorkflow: workflow });
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-test",
        toolUseId: "tool-1",
        name: "Bash",
        input: { command: "npm test" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-test",
        toolUseId: "tool-1",
        status: "ok",
        output: "passed",
      },
    ];
    const result = await hook.handler(
      args("Fixed and tests pass."),
      makeCtx(transcript, workflow),
    );
    expect(result?.action).toBe("block");
  });
}
);
