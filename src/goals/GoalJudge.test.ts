import { describe, expect, it } from "vitest";
import type { LLMEvent, LLMStreamRequest } from "../transport/LLMClient.js";
import { judgeGoalTurn, parseGoalJudgeResult } from "./GoalJudge.js";

describe("parseGoalJudgeResult", () => {
  it("accepts structured continue decisions", () => {
    expect(
      parseGoalJudgeResult('{"decision":"continue","reason":"Need to verify output"}'),
    ).toEqual({
      decision: "continue",
      reason: "Need to verify output",
    });
  });

  it("falls back to blocked on invalid output", () => {
    expect(parseGoalJudgeResult("not json").decision).toBe("blocked");
  });

  it("judges a goal turn from a bounded LLM JSON response", async () => {
    const calls: LLMStreamRequest[] = [];
    const llm = {
      async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
        calls.push(req);
        yield {
          kind: "text_delta",
          blockIndex: 0,
          delta: '{"decision":"continue","reason":"Need another pass"}',
        };
        yield {
          kind: "message_end",
          stopReason: "end_turn",
          usage: { inputTokens: 1, outputTokens: 1 },
        };
      },
    };

    const result = await judgeGoalTurn({
      llm,
      model: "judge-model",
      objective: "Ship the launch memo",
      completionCriteria: [
        "Final memo is written",
        "Risks are explicitly listed",
      ],
      userText: "Ship the launch memo",
      assistantText: "Drafted the outline.",
    });

    expect(result).toEqual({
      decision: "continue",
      reason: "Need another pass",
    });
    expect(calls[0]).toMatchObject({
      model: "judge-model",
      max_tokens: 512,
      temperature: 0,
      thinking: { type: "disabled" },
    });
    expect(String(calls[0]?.system)).toContain("goal mission judge");
    expect(JSON.stringify(calls[0]?.messages)).toContain("Completion criteria");
    expect(JSON.stringify(calls[0]?.messages)).toContain("Final memo is written");
  });
});
