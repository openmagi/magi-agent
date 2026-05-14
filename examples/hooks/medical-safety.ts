/**
 * medical-safety — beforeCommit hook that checks for drug dosage
 * information in the assistant response. Blocks the commit if
 * specific dosage recommendations are detected without a disclaimer.
 *
 * This is an example hook demonstrating the magi-agent hook system.
 */

import type {
  HookArgs,
  HookContext,
  HookResult,
  RegisteredHook,
} from "../../src/hooks/types.js";

const DOSAGE_PATTERNS = [
  /\b\d+\s*(?:mg|ml|mcg|iu|units?)(?:\s*\/\s*(?:kg|day|hour|dose))?\b/i,
  /\btake\s+\d+\s+(?:pills?|tablets?|capsules?|drops?)\b/i,
  /\b(?:dosage|dose)\s*(?::|is|of)\s*\d+/i,
];

const DISCLAIMER_PATTERNS = [
  /consult\s+(?:a\s+|your\s+)?(?:doctor|physician|healthcare|medical)/i,
  /not\s+medical\s+advice/i,
  /seek\s+(?:professional\s+)?medical/i,
];

const hook: RegisteredHook<"beforeCommit"> = {
  name: "medical-safety",
  point: "beforeCommit",
  priority: 80,
  blocking: true,
  timeoutMs: 2_000,

  async handler(
    args: HookArgs["beforeCommit"],
    ctx: HookContext,
  ): Promise<HookResult<HookArgs["beforeCommit"]> | void> {
    const text = args.assistantText;

    // Check if response contains dosage information
    const hasDosage = DOSAGE_PATTERNS.some((p) => p.test(text));
    if (!hasDosage) return { action: "continue" };

    // Check if disclaimer is present
    const hasDisclaimer = DISCLAIMER_PATTERNS.some((p) => p.test(text));
    if (hasDisclaimer) {
      ctx.log("info", "medical-safety: dosage detected with disclaimer", {});
      return { action: "continue" };
    }

    ctx.log("warn", "medical-safety: dosage without disclaimer blocked", {});
    return {
      action: "block",
      reason:
        "Response contains specific drug dosage information without a medical disclaimer. " +
        "Please add a note advising the user to consult a healthcare professional.",
    };
  },
};

export default hook;
