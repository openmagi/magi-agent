/**
 * Built-in self-claim verifier hook (port of
 * infra/docker/chat-proxy/self-claim-verifier.js — AEF RULE5).
 * Design reference: §6 invariant D (read-before-claim).
 *
 * Blocks commit when the assistant asserts something about its own
 * workspace file / prompt / memory WITHOUT having read the
 * referenced file in this turn. The model is forced to abort the
 * turn, read the file, and retry.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import { getOrClassifyFinalAnswerMeta } from "./turnMetaClassifier.js";

/** Tools whose execution counts as "having read a workspace file". */
const READ_TOOLS = new Set(["FileRead", "Grep", "Glob", "Bash"]);

const WORKSPACE_CLAIM_PATTERNS = [
  /\bmy\s+(?:workspace|config|settings?|prompt|SOUL\.md|TOOLS\.md|AGENTS\.md|MEMORY\.md|USER\.md)\b/i,
  /\b(?:workspace|config|설정|프롬프트|메모리)\s*(?:에|에는|에서|에서는|를|의|includes?|contains?|says?|has|shows?)\b/i,
  /\b(?:in|from|according to)\s+(?:my|the)\s+(?:workspace|prompt|config|memory|settings)\b/i,
  /\bSOUL\.md\s+(?:says?|contains?|includes?|specifies?|defines?|mentions?)\b/i,
  /\b(?:TOOLS|AGENTS|MEMORY|USER)\.md\s+(?:says?|contains?|includes?|specifies?|lists?)\b/i,
];

function detectSelfClaimDeterministic(text: string): boolean {
  if (!text || text.length < 10) return false;
  return WORKSPACE_CLAIM_PATTERNS.some((p) => p.test(text));
}

async function detectSelfClaim(
  text: string,
  ctx?: HookContext,
  userMessage = "",
): Promise<boolean> {
  if (!text || text.length < 10) return false;

  // P2-2: deterministic mode — regex-based detection, no LLM
  if (process.env.MAGI_DETERMINISTIC_SELF_CLAIM === "1") {
    return detectSelfClaimDeterministic(text);
  }

  if (!ctx?.llm) return detectSelfClaimDeterministic(text);

  const meta = await getOrClassifyFinalAnswerMeta(ctx, {
    userMessage,
    assistantText: text,
  });
  return meta.selfClaim;
}

export const selfClaimVerifierHook: RegisteredHook<"beforeCommit"> = {
  name: "builtin:self-claim-verifier",
  point: "beforeCommit",
  priority: 80,
  blocking: true,
  failOpen: true,
  timeoutMs: 5_000,
  handler: async ({ assistantText, toolReadHappened, userMessage }, ctx: HookContext) => {
    const hasClaim = await detectSelfClaim(assistantText, ctx, userMessage);
    if (!hasClaim) return { action: "continue" };

    if (toolReadHappened) {
      ctx.emit({
        type: "rule_check",
        ruleId: "self-claim-verifier",
        verdict: "ok",
        detail: "self-claim with tool read",
      });
      return { action: "continue" };
    }

    ctx.emit({
      type: "rule_check",
      ruleId: "self-claim-verifier",
      verdict: "violation",
      detail: "self-claim without file read",
    });
    ctx.log("warn", "blocking commit: self-claim without file read");

    return {
      action: "block",
      reason:
        "[RETRY:RULE5] Response asserted something about your own workspace / prompt / memory without having read the relevant file this turn. Memory-based claims are treated as hallucination. Read the file (FileRead on SOUL.md / AGENTS.md / TOOLS.md / MEMORY.md / USER.md or the specific file you referenced) and regenerate the answer with concrete quotes. If the file genuinely doesn't exist, say so explicitly after verifying with Glob/Bash ls.",
    };
  },
};

/**
 * Reusable helper to build a toolReadHappened flag from any Turn's
 * tool-call history. Kept here so RuleEngine (Phase 2c-next) can
 * reuse the same detection logic.
 */
export function isReadTool(name: string): boolean {
  return READ_TOOLS.has(name);
}
