/**
 * Secret exposure gate.
 *
 * Blocks final answers that appear to expose literal credentials while
 * allowing safe references to environment variable names and masked
 * last-four style reporting.
 */

import type { HookContext, RegisteredHook } from "../types.js";

const TOKEN_PATTERNS: readonly RegExp[] = [
  /\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b/,
  /\bsk-ant-[A-Za-z0-9_-]{20,}\b/,
  /\bghp_[A-Za-z0-9_]{20,}\b/,
  /\bgithub_pat_[A-Za-z0-9_]{20,}\b/,
  /\bxox[baprs]-[A-Za-z0-9-]{20,}\b/,
  /\bAKIA[0-9A-Z]{16}\b/,
];

const SECRET_ASSIGNMENT_RE =
  /\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PRIVATE_KEY)\s*=\s*["']?(?!\$|<|your_|redacted|masked|\*{3,})[A-Za-z0-9_./+=:-]{12,}/i;

function isEnabled(): boolean {
  const raw = process.env.MAGI_SECRET_EXPOSURE;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export function detectSecretExposure(text: string): boolean {
  if (!text || !text.trim()) return false;
  if (SECRET_ASSIGNMENT_RE.test(text)) return true;
  return TOKEN_PATTERNS.some((p) => p.test(text));
}

export function makeSecretExposureGateHook(): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:secret-exposure-gate",
    point: "beforeCommit",
    priority: 80,
    blocking: true,
    timeoutMs: 500,
    handler: async ({ assistantText }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!detectSecretExposure(assistantText)) return { action: "continue" };

        ctx.emit({
          type: "rule_check",
          ruleId: "secret-exposure-gate",
          verdict: "violation",
          detail: "secret-like literal detected in assistant output",
        });
        return {
          action: "block",
          reason: [
            "[RETRY:SECRET_EXPOSURE] The draft appears to expose a literal credential or token.",
            "Rewrite the answer without the secret value. Refer to credential names only, or show at most a masked last-four form if the user needs identification.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log("warn", "[secret-exposure-gate] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}

export const secretExposureGateHook = makeSecretExposureGateHook();
