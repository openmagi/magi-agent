/**
 * Built-in answer-verifier hook.
 * Design reference: §7.13 Self-verification loop.
 *
 * beforeCommit gate that asks Haiku to judge whether the assistant's
 * drafted final answer actually fulfils the user's original request.
 * Verdicts:
 *   FULFILLED   — answer directly addresses the request.
 *   PARTIAL     — addresses part; something is missing.
 *   DEFLECTION  — answer avoids / abstracts away the request.
 *   REFUSAL     — answer explicitly declines.
 *
 * Non-FULFILLED verdicts trigger a retryable block — the reason is
 * prefixed with `[RETRY:ANSWER_VERIFY:<VERDICT>]` so an outer retry
 * loop (or a human operator inspecting the log) can route the turn
 * back through another execute() pass. After the retry budget is
 * exhausted we fail open and let the commit proceed, to avoid trapping
 * a turn in an unbounded judge loop.
 *
 * Fail-open policy: Haiku timeout / transport error / unparseable
 * verdict => treat as FULFILLED. The judge is advisory safety, not a
 * correctness oracle; a broken judge must never block a turn.
 *
 * Toggle: `MAGI_ANSWER_VERIFY=off` disables the hook globally
 * (set by chat-proxy when a bot opts out via agent.config.yaml
 * `answer_verify: off`).
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";

export type AnswerVerdict = "FULFILLED" | "PARTIAL" | "DEFLECTION" | "REFUSAL";

/** Maximum retries before fail-open — §7.13 caps at 1 retry per turn. */
const MAX_RETRIES = 1;

/** Haiku deadline per §7.13.
 *
 * Bumped 3_000 → 8_000 → 15_000. Real-world p95 for Haiku cold-start
 * + first-token under load regularly tripped shorter caps, turning
 * legitimate answers into timeout aborts. Combined with `failOpen: true`
 * on the hook registration, a slow judge never blocks a commit.
 */
const DEFAULT_TIMEOUT_MS = 15_000;

/** Fallback when agentModel is unavailable. */
const JUDGE_MODEL_FALLBACK = "claude-haiku-4-5-20251001";

const JUDGE_SYSTEM = [
  "You judge whether an assistant's final answer fulfils the user's request.",
  "Respond with EXACTLY ONE WORD — one of:",
  "  FULFILLED  — answer directly addresses the request.",
  "  PARTIAL    — addresses part of the request; something is missing.",
  "  DEFLECTION — answer avoids or abstracts away the request without explicit reason.",
  "  REFUSAL    — answer explicitly declines (compliance / safety / capability).",
  "",
  "Rules:",
  "- Output only the one word, uppercase, no punctuation, no explanation.",
  "- Default to FULFILLED when uncertain.",
  "- Treat a direct, relevant answer as FULFILLED even if terse.",
].join("\n");

/** Public for tests. */
export async function judgeAnswer(
  llm: LLMClient,
  userMessage: string,
  assistantText: string,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
  model?: string,
): Promise<AnswerVerdict> {
  const deadline = Date.now() + timeoutMs;
  const judgePrompt = [
    `USER REQUEST:\n${userMessage.slice(0, 4000)}`,
    "",
    `ASSISTANT ANSWER:\n${assistantText.slice(0, 6000)}`,
    "",
    "Verdict (one word):",
  ].join("\n");

  let output = "";
  try {
    const stream = llm.stream({
      model: model ?? JUDGE_MODEL_FALLBACK,
      system: JUDGE_SYSTEM,
      messages: [{ role: "user", content: judgePrompt }],
      max_tokens: 8,
      temperature: 0,
    });
    for await (const evt of stream) {
      if (Date.now() > deadline) break;
      if (evt.kind === "text_delta") output += evt.delta;
      if (evt.kind === "message_end" || evt.kind === "error") break;
    }
  } catch {
    return "FULFILLED";
  }

  return parseVerdict(output);
}

export function parseVerdict(raw: string): AnswerVerdict {
  const token = raw.trim().toUpperCase().replace(/[^A-Z]/g, "");
  if (token.startsWith("FULFILLED")) return "FULFILLED";
  if (token.startsWith("PARTIAL")) return "PARTIAL";
  if (token.startsWith("DEFLECTION")) return "DEFLECTION";
  if (token.startsWith("REFUSAL")) return "REFUSAL";
  return "FULFILLED";
}

function isEnabled(): boolean {
  const raw = process.env.MAGI_ANSWER_VERIFY;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  if (v === "" || v === "on" || v === "true" || v === "1") return true;
  return false;
}

export const answerVerifierHook: RegisteredHook<"beforeCommit"> = {
  name: "builtin:answer-verifier",
  point: "beforeCommit",
  priority: 90,
  blocking: true,
  failOpen: true,
  timeoutMs: DEFAULT_TIMEOUT_MS + 1_000,
  handler: async (
    { userMessage, assistantText, retryCount },
    ctx: HookContext,
  ) => {
    if (!isEnabled()) return { action: "continue" };

    // Nothing to judge — empty assistant text is already handled by
    // other commit-path logic.
    if (!assistantText || assistantText.trim().length === 0) {
      return { action: "continue" };
    }
    if (!userMessage || userMessage.trim().length === 0) {
      return { action: "continue" };
    }

    const verdict = await judgeAnswer(
      ctx.llm, userMessage, assistantText,
      DEFAULT_TIMEOUT_MS, ctx.agentModel,
    );

    ctx.emit({
      type: "rule_check",
      ruleId: "answer-verifier",
      verdict: verdict === "FULFILLED" ? "ok" : "violation",
      detail: `verdict=${verdict} retryCount=${retryCount}`,
    });

    if (verdict === "FULFILLED") {
      return { action: "continue" };
    }

    // REFUSAL: per §7.13 step 6, allow commit — explicit decline is
    // legitimate; emit rule_check for the log and move on.
    if (verdict === "REFUSAL") {
      ctx.log("info", "answer-verifier: explicit refusal allowed", {
        retryCount,
      });
      return { action: "continue" };
    }

    if (retryCount >= MAX_RETRIES) {
      ctx.log("warn", "answer-verifier: retry budget exhausted; failing open", {
        verdict,
        retryCount,
      });
      return { action: "continue" };
    }

    ctx.log("warn", "answer-verifier: blocking commit for retry", {
      verdict,
      retryCount,
    });
    return {
      action: "block",
      reason: `[RETRY:ANSWER_VERIFY:${verdict}] Judge determined the answer does not fulfil the user's request (verdict=${verdict}). Re-attempt: directly address what the user asked for, name and fix the missing piece, or explicitly state why you cannot (REFUSAL).`,
    };
  },
};
