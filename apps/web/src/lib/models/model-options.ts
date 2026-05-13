import { LOCAL_LLM_MODEL_OPTIONS, isLocalLlmEnabledPlan } from "@/lib/models/local-llm";

export interface ModelOption {
  value: string;
  label: string;
}

export const BASE_MODEL_OPTIONS = [
  { value: "haiku", label: "Claude Haiku 4.5" },
  { value: "sonnet", label: "Claude Sonnet 4.5" },
  { value: "opus", label: "Claude Opus 4.6" },
  { value: "gpt_5_nano", label: "GPT-5.4 Nano" },
  { value: "gpt_5_mini", label: "GPT-5.4 Mini" },
  { value: "gpt_5_5", label: "GPT-5.5" },
  { value: "gpt_5_5_pro", label: "GPT-5.5 Pro" },
  { value: "codex", label: "Codex (OAuth Required)" },
  { value: "kimi_k2_5", label: "Kimi K2.6 (Fireworks AI)" },
  { value: "minimax_m2_7", label: "MiniMax M2.7 (Fireworks AI)" },
  { value: "gemini_3_1_flash_lite", label: "Gemini 3.1 Flash Lite (Google)" },
  { value: "gemini_3_1_pro", label: "Gemini 3.1 Pro (Google)" },
] as const satisfies readonly ModelOption[];

export function normalizeModelSelectionForSettings(value: string): string {
  if (value === "gpt_5_1") return "gpt_5_mini";
  if (value === "gpt_5_4") return "gpt_5_5";
  return value;
}

export function getModelOptions(subscriptionPlan: string | null | undefined): ModelOption[] {
  const baseOptions = [...BASE_MODEL_OPTIONS];
  if (!isLocalLlmEnabledPlan(subscriptionPlan)) return baseOptions;
  return [
    ...baseOptions,
    ...LOCAL_LLM_MODEL_OPTIONS.map((model) => ({
      value: model.value,
      label: model.label,
    })),
  ];
}
