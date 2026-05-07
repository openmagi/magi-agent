import { describe, expect, it, vi } from "vitest";
import type { HookContext } from "../types.js";
import { sessionCommitmentTrackerHook } from "./sessionCommitmentTracker.js";

function makeCtx(): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
}

describe("sessionCommitmentTracker", () => {
  it("treats localized route META values as canonical route commitments", async () => {
    const result = await sessionCommitmentTrackerHook.handler(
      {
        messages: [
          { role: "user", content: "긴 보고서 작성해줘" },
          {
            role: "assistant",
            content:
              "[META: intent=실행, domain=문서작성, complexity=복잡, route=서브에이전트]\n시작하겠습니다.",
          },
          { role: "user", content: "상태 어때?" },
        ],
        tools: [],
        system: "base system",
        iteration: 0,
      },
      makeCtx(),
    );

    expect(result?.action).toBe("replace");
    if (result?.action === "replace") {
      expect(result.value.system).toContain("route=subagent");
    }
  });
});
