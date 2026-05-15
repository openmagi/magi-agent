// Re-export all database row types from the single source of truth
import type { OrgRole, InviteStatus } from "@/lib/supabase/types";

export type {
  Profile,
  Bot,
  Subscription,
  Credit,
  CreditTransaction,
  UsageLog,
  BotInsert,
  BotUpdate,
  ModelSelection,
  ApiKeyMode,
  BotStatus,
  HealthStatus,
  SubscriptionPlan,
  SubscriptionStatus,
  TransactionType,
  OrgRole,
  InviteStatus,
  KbScope,
} from "@/lib/supabase/types";

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
// These match the `.select()` projections returned from the API to the client.

/** Fields returned by GET /api/bots and used by dashboard components */
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

/** Minimal bot data used by the onboarding deploy step */
export interface BotDeployData {
  id: string;
  name: string;
  status: string;
  telegram_bot_username?: string;
}

/** Bot data used by the settings form */
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
