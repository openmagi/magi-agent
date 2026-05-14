/**
 * Provider health verifier — beforeCommit, priority 81.
 *
 * Deterministic harness-level control for degraded AI provider states.
 * This hook does not ask the model to self-assess. It raises the
 * grounding requirement for exactness-sensitive answers when the
 * platform control layer says the current provider is degraded/outage.
 */

import type { RegisteredHook, HookContext } from "../types.js";

const MAX_RETRIES = 1;

const EXACTNESS_PATTERNS: RegExp[] = [
  /deploy|deployment|production|prod|rollout|rollback|vercel|k8s|kubernetes|infra|incident|outage|status/i,
  /security|secret|auth|billing|payment|stripe|wallet|migration|database|supabase/i,
  /legal|law|medical|health|financial|finance|market|stock|tax|accounting|audit/i,
  /file|document|spreadsheet|pdf|docx|xlsx|kb|knowledge|config|setting|script|code|workspace/i,
  /배포|프로덕션|운영|롤백|장애|상태|보안|시크릿|인증|결제|마이그레이션|데이터베이스/u,
  /법률|의료|건강|재무|금융|주식|세금|회계|감사|파일|문서|설정|스크립트|코드|작업공간|워크스페이스/u,
];

function isEnabled(): boolean {
  const raw = process.env.MAGI_PROVIDER_HEALTH_VERIFIER;
  if (raw === undefined || raw === null) return true;
  const value = raw.trim().toLowerCase();
  return value === "" || value === "on" || value === "true" || value === "1";
}

export function isExactnessSensitiveProviderHealthTask(userMessage: string): boolean {
  return EXACTNESS_PATTERNS.some((pattern) => pattern.test(userMessage));
}

function isProviderDegraded(ctx: HookContext): boolean {
  const state = ctx.providerHealth?.state;
  return state === "degraded" || state === "outage";
}

export function makeProviderHealthVerifierHook(): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:provider-health-verifier",
    point: "beforeCommit",
    priority: 81,
    blocking: true,
    timeoutMs: 1_000,
    handler: async ({ userMessage, toolReadHappened, retryCount }, ctx: HookContext) => {
      if (!isEnabled()) return { action: "continue" };
      if (!isProviderDegraded(ctx)) return { action: "continue" };
      if (!isExactnessSensitiveProviderHealthTask(userMessage)) return { action: "continue" };
      if (toolReadHappened) return { action: "continue" };

      const provider = ctx.providerHealth?.provider || "unknown";
      const state = ctx.providerHealth?.state || "unknown";
      const summary = ctx.providerHealth?.summary || "provider health signal";

      if (retryCount >= MAX_RETRIES) {
        ctx.log("warn", "[provider-health-verifier] retry exhausted; failing open", {
          provider,
          state,
          summary,
        });
        ctx.emit({
          type: "rule_check",
          ruleId: "provider-health-verifier",
          verdict: "violation",
          detail: `retry exhausted under provider=${provider} state=${state}; failing open`,
        });
        return { action: "continue" };
      }

      ctx.emit({
        type: "rule_check",
        ruleId: "provider-health-verifier",
        verdict: "violation",
        detail: `provider=${provider} state=${state}; exactness-sensitive answer needs same-turn evidence`,
      });

      return {
        action: "block",
        reason: [
          "[RETRY:PROVIDER_HEALTH] Current provider health signals are degraded for this turn.",
          "For exactness-sensitive work, do not answer from memory alone.",
          "",
          "Before finalising this answer:",
          "1) Use the relevant read/search/status/tool command for the claim.",
          "2) Base the answer on that same-turn evidence.",
          "3) If no deterministic evidence is available, state the uncertainty and the verification gap.",
        ].join("\n"),
      };
    },
  };
}
