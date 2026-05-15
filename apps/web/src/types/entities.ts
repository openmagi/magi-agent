// Local-mode type definitions (no Supabase dependency)

export type ModelSelection = "smart_routing" | "haiku" | "sonnet" | "opus" | "kimi_k2_5" | "minimax_m2_5" | "minimax_m2_7" | "gpt_5_nano" | "gpt_5_1" | "gpt_5_mini" | "gpt_5_5" | "gpt_5_5_pro" | "gpt_smart_routing" | "codex" | "clawy_smart_routing" | "local_gemma_fast" | "local_gemma_max" | "local_qwen_uncensored" | "gemini_2_5_flash" | "gemini_2_5_pro" | "gemini_3_1_flash_lite" | "gemini_3_1_pro";
export type ApiKeyMode = "byok" | "platform_credits";
export type BotStatus = "provisioning" | "active" | "stopped" | "error" | "deleted";

/** Fields used by dashboard components */
export interface BotCardData {
  id: string;
  name: string;
  status: string;
  model_selection: string;
  router_type?: string;
  api_key_mode: string;
  created_at: string;
  bot_purpose?: string | null;
  purpose_preset?: string | null;
  error_message?: string | null;
  disabled_skills?: string[] | null;
  agent_rules?: string | null;
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
  language: string;
  agent_rules: string | null;
  disabled_skills: string[];
}
