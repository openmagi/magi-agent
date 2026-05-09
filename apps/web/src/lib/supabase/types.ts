export type ModelSelection =
  | "smart_routing"
  | "haiku"
  | "sonnet"
  | "opus"
  | "kimi_k2_5"
  | "minimax_m2_7"
  | "gpt_5_nano"
  | "gpt_5_mini"
  | "gpt_5_5"
  | "gpt_5_5_pro"
  | "gpt_smart_routing"
  | "codex"
  | "magi_smart_routing"
  | "local_gemma_fast"
  | "local_gemma_max"
  | "local_qwen_uncensored"
  | "gemini_3_1_flash_lite"
  | "gemini_3_1_pro";

export type ApiKeyMode = "byok" | "platform_credits";
export type SubscriptionPlan = "byok" | "pro" | "pro_plus" | "max" | "flex";
