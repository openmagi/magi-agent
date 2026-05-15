import { env } from "@/lib/config";
import type { SubscriptionPlan } from "@/lib/supabase/types";

/** Plan rank for upgrade/downgrade determination */
export const PLAN_RANK: Record<string, number> = {
  byok: 0,
  pro: 1,
  pro_plus: 2,
  max: 3,
  flex: 4,
};

/** Monthly subscription price in cents */
export const PLAN_PRICE_CENTS: Record<string, number> = {
  byok: 799,
  pro: 1499,
  pro_plus: 8999,
  max: 39900,
  flex: 199900,
};

/** Monthly non-accumulating LLM credits in cents after managed hosting allocation */
export const PLAN_MONTHLY_CREDITS_CENTS: Record<string, number> = {
  pro: 500,
  pro_plus: 8000,
  max: 35000,
  flex: 190000,
};

/** Monthly Brave Search free quota (overage billed at $0.01/search from credits) */
export const PLAN_SEARCH_QUOTA: Record<string, number> = {
  byok: 200,
  pro: 500,
  pro_plus: 1000,
  max: 4800,
  flex: 25000,
};

/** Monthly outbound email quota per plan (stub — conservative defaults
 * until Kevin's email-quota WIP finalises numbers). Referenced by
 * src/app/api/email-quota/route.ts + src/lib/services/email-service.ts. */
export const PLAN_EMAIL_QUOTA: Record<string, number> = {
  byok: 100,
  pro: 500,
  pro_plus: 2000,
  max: 10000,
  flex: 50000,
};

/** Knowledge Base storage quota in bytes per plan */
export const PLAN_KB_QUOTA_BYTES: Record<string, number> = {
  byok: 0,
  pro: 5 * 1024 ** 3,         // 5 GB
  pro_plus: 50 * 1024 ** 3,   // 50 GB
  max: 500 * 1024 ** 3,       // 500 GB
  flex: 2 * 1024 ** 4,        // 2 TB
};

/** Maximum bots per user per plan */
export const PLAN_MAX_BOTS: Record<string, number> = {
  byok: 1,
  pro: 1,
  pro_plus: 1,
  max: 5,
  flex: 10,
};

/** Plans that support Discord integration */
export const DISCORD_ENABLED_PLANS = ["max", "flex"] as const;

export function isUpgrade(from: string, to: string): boolean {
  return (PLAN_RANK[to] ?? 0) > (PLAN_RANK[from] ?? 0);
}

export function isDowngrade(from: string, to: string): boolean {
  return (PLAN_RANK[to] ?? 0) < (PLAN_RANK[from] ?? 0);
}

type BillingInterval = "monthly" | "yearly";

function getYearlyPriceId(plan: string): string | undefined {
  if (plan === "flex") return env.STRIPE_FLEX_YEARLY_PRICE_ID;
  if (plan === "max") return env.STRIPE_MAX_YEARLY_PRICE_ID;
  if (plan === "pro_plus") return env.STRIPE_PRO_PLUS_YEARLY_PRICE_ID;
  if (plan === "pro") return env.STRIPE_PRO_YEARLY_PRICE_ID;
  return undefined;
}

export function getPriceId(plan: string, billingInterval: BillingInterval = "monthly"): string {
  if (billingInterval === "yearly") {
    const yearlyPriceId = getYearlyPriceId(plan);
    if (yearlyPriceId) return yearlyPriceId;
  }
  if (plan === "flex") {
    if (!env.STRIPE_FLEX_PRICE_ID) throw new Error("STRIPE_FLEX_PRICE_ID not configured");
    return env.STRIPE_FLEX_PRICE_ID;
  }
  if (plan === "max") {
    if (!env.STRIPE_MAX_PRICE_ID) throw new Error("STRIPE_MAX_PRICE_ID not configured");
    return env.STRIPE_MAX_PRICE_ID;
  }
  if (plan === "pro_plus") return env.STRIPE_PRO_PLUS_PRICE_ID;
  if (plan === "pro") return env.STRIPE_PRO_PRICE_ID;
  return env.STRIPE_BYOK_PRICE_ID;
}

export function getApiKeyMode(plan: string): "byok" | "platform_credits" {
  return plan === "byok" ? "byok" : "platform_credits";
}

/** Fireworks-hosted model identifiers */
const FIREWORKS_MODELS = ["kimi_k2_5", "minimax_m2_7"];

/** Check if a model is hosted on Fireworks AI */
export function isFireworksModel(model: string): boolean {
  return FIREWORKS_MODELS.includes(model);
}

/** OpenAI GPT model identifiers */
const OPENAI_MODELS = ["gpt_5_nano", "gpt_5_mini", "gpt_5_5", "gpt_5_5_pro", "gpt_smart_routing"];

/** Check if a model is an OpenAI GPT model */
export function isOpenAIModel(model: string): boolean {
  return OPENAI_MODELS.includes(model);
}

/** Check if a model is OpenAI Codex */
export function isCodexModel(model: string): boolean {
  return model === "codex";
}

/** Google Gemini model identifiers */
const GOOGLE_MODELS = ["gemini_2_5_flash", "gemini_2_5_pro", "gemini_3_1_flash_lite", "gemini_3_1_pro"];

/** Check if a model is a Google Gemini model */
export function isGoogleModel(model: string): boolean {
  return GOOGLE_MODELS.includes(model);
}

/** Check if a model is the cross-provider Open Magi Router */
export function isOpenMagiRouterModel(model: string): boolean {
  return model === "clawy_smart_routing";
}

/** All valid plan identifiers */
export const VALID_PLANS: SubscriptionPlan[] = ["byok", "pro", "pro_plus", "max", "flex"];
