/**
 * Entity types for OSS magi-agent web dashboard.
 *
 * The cloud product derives these from Supabase generated types.
 * OSS defines them inline since there is no Supabase dependency.
 */

// ── Scalar enums ────────────────────────────────────────────────────────────

export type ModelSelection = string;
export type ApiKeyMode = "byok" | "platform_credits";
export type BotStatus = "active" | "provisioning" | "error" | "deleted" | "suspended";
export type HealthStatus = "healthy" | "degraded" | "down" | "unknown";
export type SubscriptionPlan = "byok" | "pro" | "pro_plus" | "max" | "flex";
export type SubscriptionStatus = "active" | "trialing" | "past_due" | "canceled" | "incomplete";
export type TransactionType = "credit" | "debit" | "refund";
export type OrgRole = "owner" | "admin" | "member";
export type InviteStatus = "pending" | "accepted" | "expired";
export type KbScope = "personal" | "org" | "all";

// ── Row types (stubs) ───────────────────────────────────────────────────────

export interface Profile { id: string; email?: string; display_name?: string }
export interface Bot { id: string; name: string; status: BotStatus; model_selection: string }
export interface Subscription { id: string; plan: SubscriptionPlan; status: SubscriptionStatus }
export interface Credit { id: string; balance: number }
export interface CreditTransaction { id: string; amount: number; type: TransactionType }
export interface UsageLog { id: string; date: string; tokens: number }
export type BotInsert = Partial<Bot>;
export type BotUpdate = Partial<Bot>;

// ── Organization UI Types ────────────────────────────────────────────────────

export interface OrgData {
  id: string;
  name: string;
  slug: string;
  owner_id: string;
  credit_balance: number;
  bot_template: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface OrgMemberData {
  user_id: string;
  role: OrgRole;
  joined_at: string;
  display_name: string | null;
  email: string | null;
  bot_count: number;
}

export interface OrgInviteData {
  id: string;
  email: string;
  status: InviteStatus;
  invited_by: string;
  expires_at: string;
  created_at: string;
}

export interface OrgSummaryData {
  id: string;
  summary: string;
  member_count: number;
  credits_used: number;
  created_at: string;
}

// ── UI Projection Types ─────────────────────────────────────────────────────

export interface BotCardData {
  id: string;
  name: string;
  status: string;
  model_selection: string;
  router_type?: string;
  telegram_bot_username: string | null;
  telegram_owner_id: number | null;
  discord_bot_username?: string | null;
  api_key_mode: string;
  deployed_version?: string;
  created_at: string;
  bot_purpose?: string | null;
  purpose_preset?: string | null;
  error_message?: string | null;
  provisioning_step?: string | null;
  updated_at?: string;
  privy_wallet_address?: string | null;
  disabled_skills?: string[] | null;
  agent_rules?: string | null;
}

export interface BotDeployData {
  id: string;
  name: string;
  status: string;
  telegram_bot_username?: string;
}

export interface BotSettingsData {
  id: string;
  name: string;
  status: string;
  model_selection: string;
  router_type?: string;
  api_key_mode: string;
  bot_purpose: string | null;
  purpose_preset: string | null;
  telegram_bot_username: string | null;
  language: string;
  agent_skill_md: string | null;
  agent_rules: string | null;
  registry_agent_id: string | null;
  privy_wallet_address: string | null;
  has_anthropic_key: boolean;
  has_fireworks_key: boolean;
  has_openai_key: boolean;
  has_gemini_key: boolean;
  has_codex_token: boolean;
  disabled_skills: string[];
}
