import type { LLMClient } from "../transport/LLMClient.js";

export type GoalDecision = "done" | "continue" | "blocked" | "needs_user";

export interface GoalJudgeResult {
  decision: GoalDecision;
  reason: string;
}

export interface JudgeGoalTurnInput {
  llm: Pick<LLMClient, "stream">;
  model: string;
  objective: string;
  userText: string;
  assistantText: string;
  signal?: AbortSignal;
}

export function parseGoalJudgeResult(text: string): GoalJudgeResult {
  try {
    const parsed = JSON.parse(text) as { decision?: unknown; reason?: unknown };
    if (
      parsed.decision === "done" ||
      parsed.decision === "continue" ||
      parsed.decision === "blocked" ||
      parsed.decision === "needs_user"
    ) {
      return {
        decision: parsed.decision,
        reason: typeof parsed.reason === "string" ? parsed.reason.slice(0, 500) : "",
      };
    }
  } catch {
    // fall through
  }
  return {
    decision: "blocked",
    reason: "Goal judge returned invalid structured output",
  };
}

export async function judgeGoalTurn(
  input: JudgeGoalTurnInput,
): Promise<GoalJudgeResult> {
  let text = "";
  for await (const event of input.llm.stream({
    model: input.model,
    system: [
      "You are a goal mission judge.",
      "Return only compact JSON with shape:",
      '{"decision":"done|continue|blocked|needs_user","reason":"short reason"}',
      "Use continue only when another autonomous turn is likely to make concrete progress.",
      "Use needs_user when progress requires user input or approval.",
      "Use blocked when the assistant failed, looped, or lacks enough context.",
    ].join("\n"),
    messages: [
      {
        role: "user",
        content: [
          `Goal: ${input.objective}`,
          `Latest user/continuation request: ${input.userText}`,
          `Latest assistant result: ${input.assistantText}`,
          "Should the runtime continue this goal automatically?",
        ].join("\n\n"),
      },
    ],
    max_tokens: 512,
    temperature: 0,
    thinking: { type: "disabled" },
    signal: input.signal,
  })) {
    if (event.kind === "text_delta") text += event.delta;
    if (event.kind === "error") {
      return { decision: "blocked", reason: event.message.slice(0, 500) };
    }
  }
  return parseGoalJudgeResult(text);
}
