import type { Database } from "./database.types";

export type Profile = Database["public"]["Tables"]["profiles"]["Row"];
export type Bot = Database["public"]["Tables"]["bots"]["Row"];
export type Subscription = Database["public"]["Tables"]["subscriptions"]["Row"];
export type Credit = Database["public"]["Tables"]["credits"]["Row"];
export type CreditTransaction = Database["public"]["Tables"]["credit_transactions"]["Row"];
export type UsageLog = Database["public"]["Tables"]["usage_logs"]["Row"];

export type BotInsert = Database["public"]["Tables"]["bots"]["Insert"];
export type BotUpdate = Database["public"]["Tables"]["bots"]["Update"];

export type ModelSelection = "smart_routing" | "haiku" | "sonnet" | "opus" | "kimi_k2_5" | "minimax_m2_5" | "minimax_m2_7" | "gpt_5_nano" | "gpt_5_1" | "gpt_5_mini" | "gpt_5_5" | "gpt_5_5_pro" | "gpt_smart_routing" | "codex" | "clawy_smart_routing" | "local_gemma_fast" | "local_gemma_max" | "local_qwen_uncensored" | "gemini_2_5_flash" | "gemini_2_5_pro" | "gemini_3_1_flash_lite" | "gemini_3_1_pro";
export type ApiKeyMode = "byok" | "platform_credits";
export type BotStatus = "provisioning" | "active" | "stopped" | "error" | "deleted";
export type HealthStatus = "healthy" | "unhealthy" | "unknown";
export type SubscriptionPlan = "byok" | "pro" | "pro_plus" | "max" | "flex";
export type SubscriptionStatus = "active" | "past_due" | "canceled" | "trialing";
export type TransactionType = "purchase" | "usage" | "refund" | "bonus";

export type UserConsent = Database["public"]["Tables"]["user_consents"]["Row"];
export type ConsentType = "analytics" | "policy_acceptance";
export type ConsentStatus = "accepted" | "declined";

export type SkillExecution = Database["public"]["Tables"]["skill_executions"]["Row"];
export type SkillAnalyticsDaily = Database["public"]["Tables"]["skill_analytics_daily"]["Row"];
export type SkillOutcome = "ok" | "partial" | "fail";

// ── Organization types (migration 077) ──────────────────────────────────────
export type OrgRole = "admin" | "member";
export type InviteStatus = "pending" | "accepted" | "expired";
export type KbScope = "personal" | "org";
