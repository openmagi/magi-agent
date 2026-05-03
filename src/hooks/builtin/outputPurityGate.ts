/**
 * Output purity gate.
 *
 * Blocks accidental leakage of hidden planning / draft meta text in
 * final answers. This complements prompt-level "do not expose chain of
 * thought" rules with a commit gate backed by the shared final-answer
 * meta classifier.
 */

import type { HookContext, RegisteredHook } from "../types.js";
import { getOrClassifyFinalAnswerMeta } from "./turnMetaClassifier.js";

const MAX_RETRIES = 1;

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_OUTPUT_PURITY;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export async function matchesInternalReasoningLeak(
  text: string,
  ctx?: HookContext,
  userMessage = "",
): Promise<boolean> {
  if (!text || !text.trim()) return false;
  if (!ctx?.llm) return false;

  const firstParagraph = text.trim().split(/\n\s*\n/, 1)[0] ?? text.trim();
  if (firstParagraph.length < 10) return false;

  const meta = await getOrClassifyFinalAnswerMeta(ctx, {
    userMessage,
    assistantText: text,
  });
  return meta.internalReasoningLeak;
}

export function makeOutputPurityGateHook(): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:output-purity-gate",
    point: "beforeCommit",
    priority: 84,
    blocking: true,
    timeoutMs: 5_000,
    handler: async ({ assistantText, retryCount, userMessage }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!(await matchesInternalReasoningLeak(assistantText, ctx, userMessage))) {
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
