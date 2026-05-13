/**
 * content-moderation — beforeCommit hook that detects PII
 * (personally identifiable information) in the assistant response.
 * Blocks the commit if PII patterns are found.
 *
 * This is an example hook demonstrating the magi-agent hook system.
 */

import type {
  HookArgs,
  HookContext,
  HookResult,
  RegisteredHook,
} from "../../src/hooks/types.js";

const PII_PATTERNS: Array<{ name: string; pattern: RegExp }> = [
  {
    name: "SSN",
    pattern: /\b\d{3}-\d{2}-\d{4}\b/,
  },
  {
    name: "credit-card",
    pattern: /\b(?:\d{4}[-\s]?){3}\d{4}\b/,
  },
  {
    name: "email",
    // Only flag when an email appears to be a real person's address
    // (not example.com or test domains)
    pattern:
      /\b[a-zA-Z0-9._%+-]+@(?!example\.com|test\.com)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b/,
  },
  {
    name: "phone-US",
    pattern: /\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b/,
  },
];

const hook: RegisteredHook<"beforeCommit"> = {
  name: "content-moderation",
  point: "beforeCommit",
  priority: 75,
  blocking: true,
  timeoutMs: 2_000,

  async handler(
    args: HookArgs["beforeCommit"],
    ctx: HookContext,
  ): Promise<HookResult<HookArgs["beforeCommit"]> | void> {
    const text = args.assistantText;
    const detected: string[] = [];

    for (const { name, pattern } of PII_PATTERNS) {
      if (pattern.test(text)) {
        detected.push(name);
      }
    }

    if (detected.length === 0) return { action: "continue" };

    ctx.log("warn", "content-moderation: PII detected", {
      types: detected,
    });
    return {
      action: "block",
      reason:
        `Response contains personally identifiable information (${detected.join(", ")}). ` +
        "Please redact PII before responding.",
    };
  },
};

export default hook;
