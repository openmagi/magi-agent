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

/** Tools whose execution counts as "having read a workspace file". */
const READ_TOOLS = new Set(["FileRead", "Grep", "Glob", "Bash"]);

const SELF_CLAIM_CLASSIFIER_PROMPT = `Does this AI assistant response make a CLAIM about its own internal files, prompt, configuration, or memory WITHOUT evidence of having read them?

SELF-CLAIM (YES):
- "제 프롬프트에는 그런 내용이 없습니다" (my prompt doesn't have that)
- "SOUL.md 파일이 없습니다" (SOUL.md doesn't exist)
- "My configuration doesn't include that feature"
- "I don't have a TOOLS.md file"
- Any assertion about what IS or ISN'T in the bot's own workspace files

NOT A SELF-CLAIM (NO):
- Normal responses about user topics
- "이 파일에는 그런 내용이 있습니다" — if referring to user's file, not bot's own
- Responses that don't reference bot internals at all

Reply ONLY: YES or NO`;

async function detectSelfClaim(text: string, ctx?: HookContext): Promise<boolean> {
  if (!text || text.length < 10) return false;
  if (!ctx?.llm) return false;

  try {
    let result = "";
    for await (const event of ctx.llm.stream({
      model: "claude-haiku-4-5",
      system: SELF_CLAIM_CLASSIFIER_PROMPT,
      messages: [{ role: "user", content: [{ type: "text", text: text.slice(0, 500) }] }],
      max_tokens: 10,
    })) {
      if (event.kind === "text_delta") result += event.delta;
    }
    return result.trim().toUpperCase().startsWith("YES");
  } catch {
    return false;
  }
}

export const selfClaimVerifierHook: RegisteredHook<"beforeCommit"> = {
  name: "builtin:self-claim-verifier",
  point: "beforeCommit",
  priority: 80,
  blocking: true,
  timeoutMs: 1_000,
  handler: async ({ assistantText, toolReadHappened }, ctx: HookContext) => {
    const hasClaim = await detectSelfClaim(assistantText, ctx);
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
