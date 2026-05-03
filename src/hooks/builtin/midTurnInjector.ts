/**
 * Mid-turn injection hook (#86) — drains Session.pendingInjections at
 * the start of each LLM iteration and appends them to the beforeLLMCall
 * `messages` as synthetic `{ role: "user", content: [...] }` blocks.
 *
 * Design reference:
 *   docs/plans/2026-04-20-message-queue-mid-turn-injection-design.md
 *
 * Fail-open: any error during drain/inject is logged and the turn
 * continues unmodified. This is an ergonomic feature, not a correctness
 * gate — a broken injector must never abort a turn.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { LLMMessage } from "../../transport/LLMClient.js";
import type { Session } from "../../Session.js";

export interface MidTurnInjectorAgent {
  /** Look up an active session by its key. Undefined if the session
   * has been evicted (unlikely mid-turn, but fail-open if so). */
  getSession(sessionKey: string): Session | undefined;
}

export interface MidTurnInjectorOpts {
  readonly agent: MidTurnInjectorAgent;
}

/**
 * Format a drained injection into a synthetic user-role LLM message.
 * The model-facing wrapper deliberately avoids the word "injection":
 * these are normal user follow-ups, and naming them as injection-like
 * content caused models to misclassify legitimate user text as prompt
 * injection attempts.
 */
export function wrapInjection(index: number, text: string, receivedAtIso: string): string {
  return [
    `<follow_up_user_message seq="${index}" at="${receivedAtIso}">`,
    "<!-- The user sent this message while you were already working on",
    "their previous request. Incorporate it into the rest of this turn -->",
    text,
    "</follow_up_user_message>",
  ].join("\n");
}

export function buildInjectionMessages(
  injections: ReadonlyArray<{ text: string; receivedAt: number }>,
  startSeq = 1,
): LLMMessage[] {
  return injections.map((inj, i) => ({
    role: "user",
    content: [
      {
        type: "text" as const,
        text: wrapInjection(
          startSeq + i,
          inj.text,
          new Date(inj.receivedAt).toISOString(),
        ),
      },
    ],
  }));
}

export function makeMidTurnInjectorHook(
  opts: MidTurnInjectorOpts,
): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:mid-turn-injector",
    point: "beforeLLMCall",
    // Priority 3: after identity/memory (1/5) but before discipline (10+)
    // so the injection lands close to the user-message end of the stack
    // but doesn't miss any system-prompt rewrites.
    priority: 3,
    blocking: false,
    handler: async (args, ctx: HookContext) => {
      try {
        const session = opts.agent.getSession(ctx.sessionKey);
        if (!session) return { action: "continue" };
        if (!session.hasPendingInjections()) return { action: "continue" };

        const drained = session.drainPendingInjections();
        if (drained.length === 0) return { action: "continue" };

        const injectionMessages = buildInjectionMessages(drained);

        ctx.log("info", "[mid-turn-injector] drained injections", {
          count: drained.length,
          iteration: args.iteration,
        });

        ctx.emit({
          type: "injection_drained",
          count: drained.length,
          iteration: args.iteration,
        } as never);

        return {
          action: "replace",
          value: {
            ...args,
            messages: [...args.messages, ...injectionMessages],
          },
        };
      } catch (err) {
        ctx.log("warn", "[mid-turn-injector] drain failed; turn continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}
