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

async function detectSelfClaim(
  text: string,
  ctx?: HookContext,
  userMessage = "",
): Promise<boolean> {
  if (!text || text.length < 10) return false;
  if (!ctx?.llm) return false;

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
