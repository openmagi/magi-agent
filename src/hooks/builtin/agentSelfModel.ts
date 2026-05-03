/**
 * Agent self-model injector (Layer 1 of the meta-cognitive scaffolding
 * — docs/plans/2026-04-20-agent-self-model-design.md).
 *
 * Prepends a fixed `<agent_self_model>` block to the system prompt at
 * the start of every turn iteration. Priority 0 so identityInjector /
 * memoryInjector / discipline all layer on top of this foundation.
 *
 * Why a hook, not a literal constant in buildSystemPrompt: makes the
 * block individually toggleable (CORE_AGENT_SELF_MODEL=off) for A/B
 * tests and lets us evolve the prompt without churning Turn.ts.
 *
 * Fail-open: any error here is logged and the turn continues without
 * the self-model block. The block is a default-reflex nudge, not a
 * correctness gate.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import { AGENT_SELF_MODEL_BLOCK } from "../../prompt/RuntimePromptBlocks.js";

/**
 * The prompt block. Exported for tests + for the preRefusalVerifier
 * hook, which uses the same language to justify its retry reasons.
 */
export { AGENT_SELF_MODEL_BLOCK };

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_SELF_MODEL;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export const agentSelfModelHook: RegisteredHook<"beforeLLMCall"> = {
  name: "builtin:agent-self-model",
  point: "beforeLLMCall",
  // Priority 0 — runs FIRST, before identity / memory / discipline.
  // Everything else layers on top of this foundation.
  priority: 0,
  blocking: false,
  handler: async (args, ctx: HookContext) => {
    try {
      if (!isEnabled()) return { action: "continue" };

      // Only need to inject on iteration 0 — subsequent iterations
      // already carry the block in `system` (Turn.ts threads
      // `system` through each loop iteration).
      if (args.iteration > 0) return { action: "continue" };

      // Idempotency guard: if for any reason the block is already
      // present (e.g. a previous hook also added it, or the test
      // harness pre-populated), don't double-inject.
      if (args.system.includes("<agent_self_model>")) {
        return { action: "continue" };
      }

      const nextSystem = `${AGENT_SELF_MODEL_BLOCK}\n\n${args.system}`;
      return {
        action: "replace",
        value: { ...args, system: nextSystem },
      };
    } catch (err) {
      ctx.log("warn", "[agent-self-model] inject failed; turn continues", {
        error: err instanceof Error ? err.message : String(err),
      });
      return { action: "continue" };
    }
  },
};
