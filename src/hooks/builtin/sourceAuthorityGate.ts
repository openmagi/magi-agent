import type { HookContext, RegisteredHook } from "../types.js";
import { getOrClassifyFinalAnswerMeta } from "./turnMetaClassifier.js";

const MAX_RETRIES = 1;

function isEnabled(): boolean {
  const raw = process.env.MAGI_SOURCE_AUTHORITY_GATE;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function hasSourceAuthorityRisk(ctx: HookContext): boolean {
  const records = ctx.executionContract?.sourceAuthorityForTurn(ctx.turnId) ?? [];
  if (
    records.some(
      (record) =>
        record.longTermMemoryPolicy !== "normal" ||
        record.currentSourceKinds.length > 0,
    )
  ) {
    return true;
  }
  return (ctx.executionContract?.memoryRecallForTurn(ctx.turnId) ?? []).some(
    (record) => record.continuity === "background",
  );
}

export function makeSourceAuthorityGateHook(): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:source-authority-gate",
    point: "beforeCommit",
    priority: 82,
    blocking: true,
    failOpen: true,
    timeoutMs: 6_500,
    handler: async ({ assistantText, userMessage, retryCount }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!assistantText.trim()) return { action: "continue" };
        if (!hasSourceAuthorityRisk(ctx)) return { action: "continue" };

        const finalMeta = await getOrClassifyFinalAnswerMeta(ctx, {
          userMessage,
          assistantText,
        });
        if (!finalMeta.sourceAuthorityViolation) return { action: "continue" };

        ctx.emit({
          type: "rule_check",
          ruleId: "source-authority-gate",
          verdict: "violation",
          detail: finalMeta.reason,
        });

        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[source-authority-gate] retry exhausted; failing open", {
            retryCount,
            reason: finalMeta.reason,
          });
          return { action: "continue" };
        }

        return {
          action: "block",
          reason: [
            "[RETRY:SOURCE_AUTHORITY]",
            "Your draft used lower-authority long-term memory over the latest user message or current-turn source.",
            "Regenerate from the latest user message and current attachments/selected KB first.",
            "Use long-term memory only as allowed by the source authority contract.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log("warn", "[source-authority-gate] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}
