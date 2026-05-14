import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import {
  extractVerificationMode,
  makeTaskContractGateHook,
} from "./taskContractGate.js";
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

function args(assistantText: string, userMessage: string, retryCount = 0) {
  return {
    assistantText,
    toolCallCount: 0,
    toolReadHappened: false,
    userMessage,
    retryCount,
  };
}

const FULL_CONTRACT = "<task_contract><verification_mode>full</verification_mode></task_contract>";

describe("taskContractGate", () => {
  const originalEnv = process.env.MAGI_TASK_CONTRACT_GATE;

  beforeEach(() => {
    delete process.env.MAGI_TASK_CONTRACT_GATE;
  });

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.MAGI_TASK_CONTRACT_GATE;
    } else {
      process.env.MAGI_TASK_CONTRACT_GATE = originalEnv;
    }
  });

  it("extracts full verification mode", () => {
    expect(extractVerificationMode(FULL_CONTRACT)).toBe("full");
  });

  it("blocks sample-only language under full verification contract", async () => {
    const hook = makeTaskContractGateHook();
    const result = await hook.handler(
      args("샘플만 확인했고 완료했습니다.", FULL_CONTRACT),
      makeCtx(),
    );
    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[RETRY:TASK_CONTRACT_VERIFY]");
    }
  });

  it("continues when a full contract has same-turn verification evidence", async () => {
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-test",
        toolUseId: "tool-1",
        name: "Bash",
        input: { command: "npm run qa" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-test",
        toolUseId: "tool-1",
        status: "ok",
        output: "all checks passed",
      },
    ];
    const hook = makeTaskContractGateHook();
    const result = await hook.handler(
      args("전체 검증을 완료했습니다.", FULL_CONTRACT),
      makeCtx(transcript),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("respects MAGI_TASK_CONTRACT_GATE=off", async () => {
    process.env.MAGI_TASK_CONTRACT_GATE = "off";
    const hook = makeTaskContractGateHook();
    const result = await hook.handler(
      args("샘플만 확인했고 완료했습니다.", FULL_CONTRACT),
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("keeps full verification debug turns blocked without investigation state", async () => {
    const workflow = new DebugWorkflow();
    workflow.classifyTurn("session-test", "turn-test", "Regression failing after deploy");
    workflow.recordVerification("session-test", "turn-test", "npm run qa");
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-test",
        toolUseId: "tool-1",
        name: "Bash",
        input: { command: "npm run qa" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-test",
        toolUseId: "tool-1",
        status: "ok",
        output: "all checks passed",
      },
    ];
    const hook = makeTaskContractGateHook({ debugWorkflow: workflow });
    const result = await hook.handler(
      args("전체 검증을 완료했습니다.", FULL_CONTRACT),
      makeCtx(transcript, workflow),
    );
    expect(result?.action).toBe("block");
  });
});
