/**
 * Cron/channel delivery safety.
 *
 * Direct channel sends and scheduled announcements need explicit user
 * consent because they can disclose private work to another channel or
 * make a false future-delivery promise. This hook asks before such
 * Bash commands run.
 */

import type { HookContext, RegisteredHook } from "../types.js";

const RISKY_DELIVERY_PATTERNS: readonly RegExp[] = [
  /\bapi\.telegram\.org\/bot[^/\s]+\/send(?:Message|Document|Photo|MediaGroup)\b/i,
  /\bdiscord(?:app)?\.com\/api\/webhooks\//i,
  /\bslack\.com\/api\/chat\.postMessage\b/i,
  /\bclawy\s+cron\s+add\b(?=.*(?:--target|--channel|--announce|--notify))/i,
  /\b(?:curl|httpie|wget)\b.{0,120}\b(?:sendMessage|chat\.postMessage|webhooks)\b/i,
];

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_CHANNEL_DELIVERY_SAFETY;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function commandFromInput(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const cmd = (input as Record<string, unknown>).command;
  return typeof cmd === "string" ? cmd : "";
}

export function matchesRiskyDeliveryCommand(command: string): boolean {
  if (!command || !command.trim()) return false;
  return RISKY_DELIVERY_PATTERNS.some((p) => p.test(command));
}

export function makeCronDeliverySafetyHook(): RegisteredHook<"beforeToolUse"> {
  return {
    name: "builtin:cron-delivery-safety",
    point: "beforeToolUse",
    priority: 42,
    blocking: true,
    timeoutMs: 500,
    handler: async ({ toolName, input }, ctx: HookContext) => {
      if (!isEnabled()) return { action: "continue" };
      if (toolName !== "Bash") return { action: "continue" };

      const command = commandFromInput(input);
      if (!matchesRiskyDeliveryCommand(command)) {
        return { action: "continue" };
      }

      ctx.emit({
        type: "rule_check",
        ruleId: "cron-delivery-safety",
        verdict: "violation",
        detail: "direct channel delivery or scheduled announcement command requires confirmation",
      });
      return {
        action: "permission_decision",
        decision: "ask",
        reason: "[CHANNEL_DELIVERY_SAFETY] This command appears to send or schedule output to an external channel. Confirm the destination, content, and timing before running it.",
      };
    },
  };
}

export const cronDeliverySafetyHook = makeCronDeliverySafetyHook();
