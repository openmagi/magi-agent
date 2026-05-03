/**
 * classifyTurnMode hook — inspects the latest user message on every
 * turn's beforeLLMCall and updates Session.meta.discipline accordingly.
 *
 * Applies only when:
 *   - discipline block exists on the session, AND
 *   - discipline.frozen !== true (operator hasn't pinned a config), AND
 *   - the user message is classifiable (first non-system message of
 *     the current turn is the source).
 *
 * Labels map to discipline posture:
 *   coding      → { tdd: true,  git: true,  requireCommit: "soft" }
 *   exploratory → { tdd: false, git: true,  requireCommit: "off" }
 *   other       → no change
 *
 * Explicit user override wins: if the shared request meta classifier
 * marks `skipTdd`, tdd is set to false regardless of the mode result.
 *
 * Fail-open: any delegate / lookup error → `continue`, no mutation.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { Discipline } from "../../Session.js";
import { getOrClassifyRequestMeta } from "./turnMetaClassifier.js";

export interface ClassifyTurnModeAgent {
  getSessionDiscipline(sessionKey: string): Discipline | null;
  setSessionDiscipline(sessionKey: string, next: Discipline): void;
  /**
   * Kevin's A/A/A rule #1 — "hard mode only engages when `coding-agent`
   * skill matches". Delegate returns true when the bot's skill loader
   * has registered a tool named `coding-agent` (or a skill that
   * normalises to that name). When true AND the classifier returns
   * `"coding"`, the classifier promotes `requireCommit` from soft →
   * hard for the session. Optional — without the delegate we default
   * to soft even for strong coding signals.
   */
  isCodingAgentSkillActive?(): boolean;
}

export interface ClassifyTurnModeOptions {
  agent: ClassifyTurnModeAgent;
}

/** Extract the newest user message text from the LLM messages array. */
export function latestUserText(messages: readonly { role: string; content: unknown }[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m) continue;
    if (m.role !== "user") continue;
    const c = m.content;
    if (typeof c === "string") return c;
    if (Array.isArray(c)) {
      for (const block of c) {
        if (
          block &&
          typeof block === "object" &&
          (block as { type?: string }).type === "text" &&
          typeof (block as { text?: unknown }).text === "string"
        ) {
          return (block as { text: string }).text;
        }
      }
    }
  }
  return null;
}

export function makeClassifyTurnModeHook(
  opts: ClassifyTurnModeOptions,
): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:classify-turn-mode",
    point: "beforeLLMCall",
    priority: 3, // earliest — ahead of memory-injector (5) so the
                 //            discipline prompt block can read fresh state.
    blocking: true,
    failOpen: true,
    timeoutMs: 5_000,
    handler: async ({ messages, iteration }, ctx: HookContext) => {
      // Only classify on the first iteration of the turn (the user
      // message doesn't change within a turn).
      if (iteration > 0) return { action: "continue" };
      const current = opts.agent.getSessionDiscipline(ctx.sessionKey);
      if (!current) return { action: "continue" };
      if (current.frozen) return { action: "continue" };
      const text = latestUserText(messages);
      if (!text) return { action: "continue" };

      const classified = await getOrClassifyRequestMeta(ctx, { userMessage: text });
      const mode = classified.turnMode;
      const skip = classified.skipTdd;

      let next: Discipline | null = null;
      if (mode.label === "coding" && mode.confidence >= 0.6) {
        // Kevin's A/A/A rule #1 — "hard mode only engages when the
        // `coding-agent` skill matches AND classifyTurnMode returns
        // `code`". Skill-active → promote to hard regardless of the
        // off/soft baseline; skill-missing → keep soft as the global
        // baseline (unless an operator already pinned hard).
        const codingAgentActive =
          opts.agent.isCodingAgentSkillActive?.() === true;
        const nextCommit: Discipline["requireCommit"] =
          current.requireCommit === "hard"
            ? "hard"
            : codingAgentActive
              ? "hard"
              : "soft";
        next = {
          ...current,
          tdd: skip || current.skipTdd === true ? false : true,
          git: true,
          requireCommit: nextCommit,
          lastClassifiedMode: "coding",
        };
      } else if (mode.label === "exploratory" && mode.confidence >= 0.6) {
        next = {
          ...current,
          tdd: false,
          git: true,
          requireCommit: "off",
          lastClassifiedMode: "exploratory",
        };
      } else {
        // "other" — leave tdd/git as-is but still record the label so
        // the prompt block can show "Mode: other (discipline off)".
        next = { ...current, lastClassifiedMode: "other" };
      }

      if (skip && next.tdd) next.tdd = false;

      opts.agent.setSessionDiscipline(ctx.sessionKey, next);
      ctx.log("info", "[discipline] classified", {
        turnId: ctx.turnId,
        label: mode.label,
        confidence: mode.confidence,
        skipTdd: skip,
      });
      return { action: "continue" };
    },
  };
}
