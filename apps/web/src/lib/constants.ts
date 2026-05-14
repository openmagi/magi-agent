import { LOCAL_LLM_MODEL_OPTIONS } from "@/lib/models/local-llm";

export const MODEL_LABELS: Record<string, string> = {
  haiku: "Claude Haiku 4.5",
  sonnet: "Claude Sonnet 4.5",
  opus: "Claude Opus 4.6",
  kimi_k2_5: "Kimi K2.6",
  minimax_m2_7: "MiniMax M2.7",
  gpt_5_nano: "GPT-5.4 Nano",
  gpt_5_mini: "GPT-5.4 Mini",
  gpt_5_5: "GPT-5.5",
  gpt_5_5_pro: "GPT-5.5 Pro",
  codex: "Codex",
  gemini_3_1_flash_lite: "Gemini 3.1 Flash Lite",
  gemini_3_1_pro: "Gemini 3.1 Pro",
  ...Object.fromEntries(
    LOCAL_LLM_MODEL_OPTIONS.map((model) => [model.value, model.label]),
  ),
};

export type ValidRouterType =
  | "standard"
  | "only_claude"
  | "claude_supremacy";
