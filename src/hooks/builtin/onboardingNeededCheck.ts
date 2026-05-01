/**
 * onboardingNeededCheck hook — beforeTurnStart nudge that reminds the
 * user about the 2-minute onboarding flow, unless they're already
 * onboarded or have declined twice.
 *
 * Design reference:
 *   docs/plans/2026-04-20-superpowers-plugin-design.md (design decision #2:
 *   "Onboarding — soft hint, aggressive nudging.")
 *
 * Behaviour:
 *   - beforeTurnStart, priority 6 (after session-resume seed at 2, ahead
 *     of the later beforeLLMCall / beforeToolUse gates).
 *   - Fires only on the first-turn-of-pod for the session
 *     (`session.budgetStats().turns === 0`).
 *   - Skips when `session.meta.onboarded === true` OR
 *     `session.meta.onboardingDeclines >= 2`.
 *   - When the user's message looks like a decline (regex: no|안 할래|
 *     나중에|skip), increments the declines counter — no nudge added.
 *   - Otherwise emits an `onboarding_nudge` AgentEvent and queues a
 *     mid-turn injection so the bot receives the nudge text as a
 *     system-level user message at the very start of the LLM call.
 *
 * Fail-open: any delegate / lookup error → returns silently. Onboarding
 * is an ergonomic feature — a broken hook must never abort a turn.
 *
 * Env gate: `CORE_AGENT_ONBOARDING_STEER` (default "on").
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { Session } from "../../Session.js";

export interface OnboardingNeededCheckAgent {
  getSession(sessionKey: string): Session | undefined;
}

export interface OnboardingNeededCheckOpts {
  readonly agent: OnboardingNeededCheckAgent;
}

/**
 * Decline detector — fires for short negative replies the user might
 * send in response to a prior onboarding nudge. Both English + Korean
 * idioms are covered so native Korean bots see increments too.
 */
const DECLINE_CLASSIFIER_PROMPT = `Is this user message a DECLINE or REJECTION of an onboarding/setup suggestion?

DECLINE (YES): "no", "not now", "later", "skip", "안 할래", "나중에", "싫어", "pass", "괜찮아요 됐어요"
NOT DECLINE (NO): "괜찮아, 해볼게" (OK, let me try), "sure", "yes", "ok", "해볼까", any question, any task request

Reply ONLY: YES or NO`;

export const DECLINE_RE =
  /\b(?:no|nope|nah|not now|later|skip|pass|don't|do not|stop)\b|(?:나중에|안\s*할래|싫어|괜찮(?:아|아요)?\s*됐(?:어|어요)?)/i;

export function looksLikeDecline(text: string): boolean;
export function looksLikeDecline(text: string, ctx: HookContext): Promise<boolean>;
export function looksLikeDecline(text: string, ctx?: HookContext): boolean | Promise<boolean> {
  if (!text) return false;
  if (text.length > 200) return false; // Long messages are tasks, not declines
  const deterministic = DECLINE_RE.test(text);
  if (deterministic || !ctx?.llm || typeof ctx.llm.stream !== "function") {
    return deterministic;
  }

  return (async () => {
    try {
      let result = "";
      for await (const event of ctx.llm.stream({
        model: "claude-haiku-4-5",
        system: DECLINE_CLASSIFIER_PROMPT,
        messages: [{ role: "user", content: [{ type: "text", text: text.slice(0, 200) }] }],
        max_tokens: 5,
      })) {
        if (event.kind === "text_delta") result += event.delta;
      }
      return result.trim().toUpperCase().startsWith("YES");
    } catch {
      return deterministic;
    }
  })();
}

export function isOnboardingSteerEnabled(env: string | undefined): boolean {
  const v = (env ?? "on").trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export const ONBOARDING_NUDGE_TEXT =
  "2분 온보딩 먼저 하시죠. `/onboarding` 혹은 `/superpowers:using-superpowers` 를 실행해보세요.";

export function shouldSkipNudge(session: Session): boolean {
  if (session.meta.onboarded === true) return true;
  const declines = session.meta.onboardingDeclines ?? 0;
  if (declines >= 2) return true;
  // Only fire on the very first turn of this session (proxy for
  // "first turn of pod" — a fresh pod cannot have a session with
  // committed turns).
  if (session.budgetStats().turns > 0) return true;
  return false;
}

export function makeOnboardingNeededCheckHook(
  opts: OnboardingNeededCheckOpts,
): RegisteredHook<"beforeTurnStart"> {
  return {
    name: "builtin:onboarding-needed-check",
    point: "beforeTurnStart",
    priority: 6,
    blocking: false,
    timeoutMs: 5_000,
    handler: async ({ userMessage }, ctx: HookContext) => {
      try {
        if (!isOnboardingSteerEnabled(process.env.CORE_AGENT_ONBOARDING_STEER)) {
          return { action: "continue" };
        }
        const session = opts.agent.getSession(ctx.sessionKey);
        if (!session) return { action: "continue" };

        // Decline path first — a user saying "no" should increment the
        // counter even if they've just hit the turn cap or are already
        // onboarded (defensive — keeps the counter monotonic).
        if (await looksLikeDecline(userMessage, ctx)) {
          const next = (session.meta.onboardingDeclines ?? 0) + 1;
          session.meta.onboardingDeclines = next;
          ctx.log("info", "[onboarding-needed-check] decline recorded", {
            declines: next,
          });
          return { action: "continue" };
        }

        if (shouldSkipNudge(session)) return { action: "continue" };

        ctx.log("info", "[onboarding-needed-check] nudging first-turn user", {
          turnId: ctx.turnId,
        });
        ctx.emit({
          type: "onboarding_nudge",
          text: ONBOARDING_NUDGE_TEXT,
        } as never);

        return { action: "continue" };
      } catch (err) {
        ctx.log("warn", "[onboarding-needed-check] fail-open", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}
