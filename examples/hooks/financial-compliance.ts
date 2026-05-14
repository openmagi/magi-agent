/**
 * financial-compliance — beforeCommit hook that checks for investment
 * advice in the assistant response. Blocks specific buy/sell
 * recommendations without a disclaimer.
 *
 * This is an example hook demonstrating the magi-agent hook system.
 */

import type {
  HookArgs,
  HookContext,
  HookResult,
  RegisteredHook,
} from "../../src/hooks/types.js";

const INVESTMENT_ADVICE_PATTERNS = [
  /\b(?:you\s+should|I\s+recommend|I\s+suggest)\s+(?:buy|sell|invest|short)\b/i,
  /\b(?:buy|sell)\s+\d+\s+(?:shares?|units?|lots?)\s+of\b/i,
  /\bguaranteed\s+(?:returns?|profit|gain)/i,
  /\b(?:will|going\s+to)\s+(?:increase|decrease|rise|fall|go\s+up|go\s+down)\s+(?:by|to)\s+\d+/i,
];

const DISCLAIMER_PATTERNS = [
  /not\s+(?:financial|investment)\s+advice/i,
  /consult\s+(?:a\s+)?(?:financial|investment)\s+(?:advisor|adviser|professional)/i,
  /do\s+your\s+own\s+research/i,
  /past\s+performance\s+(?:is\s+)?not\s+(?:indicative|a\s+guarantee)/i,
];

const hook: RegisteredHook<"beforeCommit"> = {
  name: "financial-compliance",
  point: "beforeCommit",
  priority: 80,
  blocking: true,
  timeoutMs: 2_000,

  async handler(
    args: HookArgs["beforeCommit"],
    ctx: HookContext,
  ): Promise<HookResult<HookArgs["beforeCommit"]> | void> {
    const text = args.assistantText;

    const hasAdvice = INVESTMENT_ADVICE_PATTERNS.some((p) => p.test(text));
    if (!hasAdvice) return { action: "continue" };

    const hasDisclaimer = DISCLAIMER_PATTERNS.some((p) => p.test(text));
    if (hasDisclaimer) {
      ctx.log("info", "financial-compliance: advice with disclaimer", {});
      return { action: "continue" };
    }

    ctx.log("warn", "financial-compliance: investment advice blocked", {});
    return {
      action: "block",
      reason:
        "Response contains specific investment advice without a financial disclaimer. " +
        "Please add a note that this is not financial advice and recommend consulting a financial advisor.",
    };
  },
};

export default hook;
