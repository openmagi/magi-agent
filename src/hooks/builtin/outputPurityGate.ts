/**
 * Output purity gate.
 *
 * Blocks accidental leakage of hidden planning / draft meta text in
 * final answers. This complements prompt-level "do not expose chain of
 * thought" rules with a cheap deterministic commit gate.
 */

import type { HookContext, RegisteredHook } from "../types.js";

const MAX_RETRIES = 1;

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_OUTPUT_PURITY;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

const PURITY_CLASSIFIER_PROMPT = `Does this AI assistant response START with internal reasoning or planning that should be hidden from the user?

INTERNAL LEAK (YES):
- "We need to check the database first..." (planning next steps)
- "The user is asking about X, so I should..." (meta-reasoning about the request)
- "Let me think about how to approach this..." (visible chain-of-thought)
- "먼저 검색해야 합니다" (need to search first — planning, not answer)
- "사용자가 요청하고 있는 것은..." (analyzing what user wants — meta)

NOT A LEAK (NO):
- "먼저 요청사항을 정리하면..." (organizing answer for user — legitimate structure)
- "Let me explain..." (addressing user directly)
- "The file contains..." (actual answer content)
- "분석 결과를 정리해드리겠습니다" (presenting results to user)
- Any direct response to the user's question

Reply ONLY: YES or NO`;

const INTERNAL_REASONING_LEAK_RE =
  /^(?:we need(?: to)?|i should|let me think|the user (?:is asking|wants)|먼저\s+\S+\s*해야|사용자가\s+요청)/i;

export function matchesInternalReasoningLeak(text: string): boolean;
export function matchesInternalReasoningLeak(text: string, ctx: HookContext): Promise<boolean>;
export function matchesInternalReasoningLeak(text: string, ctx?: HookContext): boolean | Promise<boolean> {
  if (!text || !text.trim()) return false;

  const firstParagraph = text.trim().split(/\n\s*\n/, 1)[0] ?? text.trim();
  if (firstParagraph.length < 10) return false;
  const deterministic = INTERNAL_REASONING_LEAK_RE.test(firstParagraph);
  if (deterministic || !ctx?.llm || typeof ctx.llm.stream !== "function") {
    return deterministic;
  }

  return (async () => {
    try {
      let result = "";
      for await (const event of ctx.llm.stream({
        model: "claude-haiku-4-5",
        system: PURITY_CLASSIFIER_PROMPT,
        messages: [{ role: "user", content: [{ type: "text", text: firstParagraph.slice(0, 300) }] }],
        max_tokens: 10,
      })) {
        if (event.kind === "text_delta") result += event.delta;
      }
      return result.trim().toUpperCase().startsWith("YES");
    } catch {
      return deterministic;
    }
  })();
}

export function makeOutputPurityGateHook(): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:output-purity-gate",
    point: "beforeCommit",
    priority: 84,
    blocking: true,
    timeoutMs: 5_000,
    handler: async ({ assistantText, retryCount }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!(await matchesInternalReasoningLeak(assistantText, ctx))) {
          return { action: "continue" };
        }
        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[output-purity-gate] retry exhausted; failing open", {
            retryCount,
          });
          return { action: "continue" };
        }
        ctx.emit({
          type: "rule_check",
          ruleId: "output-purity-gate",
          verdict: "violation",
          detail: `internal reasoning leak detected; retryCount=${retryCount}`,
        });
        return {
          action: "block",
          reason: [
            "[RETRY:OUTPUT_PURITY] The draft exposes internal planning or hidden reasoning style text.",
            "Rewrite the final answer as user-facing prose only. Do not mention tool-selection thoughts, hidden analysis, or what you should do next unless it is an explicit user-visible status update.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log("warn", "[output-purity-gate] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}

export const outputPurityGateHook = makeOutputPurityGateHook();
