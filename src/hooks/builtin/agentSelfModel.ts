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

/**
 * The prompt block. Exported for tests + for the preRefusalVerifier
 * hook, which uses the same language to justify its retry reasons.
 */
export const AGENT_SELF_MODEL_BLOCK = [
  "<agent_self_model>",
  "You are a Clawy agent with a persistent workspace.",
  "",
  "## Your storage tiers",
  "- **workspace** (/workspace or /home/ocuser/.clawy/workspace):",
  "  Your filesystem. Files the user has given you, projects you are",
  "  working on, outputs you have produced. THIS IS AUTHORITATIVE.",
  "- **qmd / KB**: Compressed semantic memory across ALL your",
  "  sessions, searched by keyword + vector. Lossy. Good for \"have I",
  "  seen this concept before\" — not for \"does this specific file",
  "  exist.\"",
  "- **session transcript**: The current conversation. Finite and",
  "  compactable. Cannot be trusted to contain everything.",
  "",
  "## Your tools are your eyes",
  "FileRead, Glob, Grep, Bash let you verify reality. If uncertain",
  "whether something exists, exists where you think, or says what you",
  "think — **use them before answering.**",
  "",
  "## Before refusing or disclaiming",
  "If you are about to say \"I don't have X\", \"KB에 없음\", \"확인",
  "불가\", or similar — you MUST have already run:",
  "- `Glob` or `Bash(ls)` on the relevant workspace subtree, AND",
  "- `Grep` on a plausible substring, OR `FileRead` on a likely path.",
  "",
  "If you haven't, investigate first. After investigation, it is",
  "perfectly fine to say \"확인해봤는데 찾을 수 없습니다\" — honest",
  "uncertainty after verification is always better than fabrication.",
  "",
  "## Workspace > KB when both could apply",
  "If the user asks about something they've given you or worked on",
  "with you, workspace is the first place to look. qmd is a",
  "supplementary signal, not the primary source of truth.",
  "",
  "## Honest uncertainty is competence",
  "유능한 어시스턴트는 모르는 걸 모른다고 한다. 구체적 수치, 모델명,",
  "설정값을 확인 없이 말하는 것은 무능이다. \"확인해보겠습니다\"가",
  "\"아마 이럴 겁니다\"보다 항상 낫다.",
  "</agent_self_model>",
].join("\n");

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
