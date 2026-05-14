/**
 * Memory continuity guard.
 *
 * Recalled memory is reference material, not active conversation state.
 * This beforeCommit gate retries only when background memory is clearly
 * promoted into a new user-facing pending decision/question.
 */

import type { HookContext, RegisteredHook } from "../types.js";
import { shouldRetryStaleMemoryPromotion } from "../../reliability/MemoryContinuity.js";

const MAX_RETRIES = 1;

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_MEMORY_CONTINUITY_GUARD;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export function makeMemoryContinuityGuardHook(): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:memory-continuity-guard",
    point: "beforeCommit",
    priority: 83,
    blocking: true,
    failOpen: true,
    timeoutMs: 1_000,
    handler: async ({ assistantText, userMessage, retryCount }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        const records = ctx.executionContract?.memoryRecallForTurn(ctx.turnId) ?? [];
        if (records.length === 0) return { action: "continue" };

        const decision = shouldRetryStaleMemoryPromotion({
          latestUserText: userMessage,
          assistantText,
          records,
        });
        if (!decision.retry) {
          if (
            records.some((record) =>
              record.continuity === "background" &&
              record.distinctivePhrases.some((phrase) => assistantText.includes(phrase)),
            )
          ) {
            ctx.emit({
              type: "rule_check",
              ruleId: "memory-continuity-guard",
              verdict: "ok",
              detail: "background memory referenced passively",
            });
          }
          return { action: "continue" };
        }

        ctx.emit({
          type: "rule_check",
          ruleId: "memory-continuity-guard",
          verdict: "violation",
          detail: `stale background memory promoted phrase=${decision.phrase ?? "(unknown)"} path=${decision.path ?? "(unknown)"}`,
        });

        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[memory-continuity-guard] retry exhausted; failing open", {
            retryCount,
            phrase: decision.phrase,
            path: decision.path,
          });
          return { action: "continue" };
        }

        return {
          action: "block",
          reason: [
            "[RETRY:MEMORY_CONTINUITY]",
            "Your draft promoted recalled background memory into a current pending topic.",
            "Answer the latest user message directly. You may use background memory only as passive context unless the user explicitly asked to continue it.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log("warn", "[memory-continuity-guard] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}

export const memoryContinuityGuardHook = makeMemoryContinuityGuardHook();
