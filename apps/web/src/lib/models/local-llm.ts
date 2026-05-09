import type { ApiKeyMode, SubscriptionPlan } from "@/lib/supabase/types";

export type LocalLlmModelSelection =
  | "local_gemma_fast"
  | "local_gemma_max"
  | "local_qwen_uncensored";

export interface LocalLlmModelOption {
  value: LocalLlmModelSelection;
  label: string;
  description: string;
  runtimeModel: `local/${string}`;
  upstreamModel: string;
  contextWindow: number;
  maxOutputTokens: number;
}

export const LOCAL_LLM_MODEL_OPTIONS = [
  {
    value: "local_gemma_fast",
    label: "Gemma 4 Fast (beta)",
    description: "Fast local beta model for Max and Flex bots.",
    runtimeModel: "local/gemma-fast",
    upstreamModel: "gemma-fast",
    contextWindow: 131_072,
    maxOutputTokens: 8_192,
  },
  {
    value: "local_gemma_max",
    label: "Gemma 4 Max (beta)",
    description: "Larger local beta Gemma model for Max and Flex bots.",
    runtimeModel: "local/gemma-max",
    upstreamModel: "gemma-max",
    contextWindow: 131_072,
    maxOutputTokens: 8_192,
  },
  {
    value: "local_qwen_uncensored",
    label: "Qwen 3.5 Uncensored (beta)",
    description: "Local beta Qwen model for Max and Flex bots.",
    runtimeModel: "local/qwen-uncensored",
    upstreamModel: "qwen-uncensored",
    contextWindow: 131_072,
    maxOutputTokens: 8_192,
  },
] as const satisfies readonly LocalLlmModelOption[];

const LOCAL_LLM_MODEL_MAP = new Map<string, LocalLlmModelOption>(
  LOCAL_LLM_MODEL_OPTIONS.map((model) => [model.value, model]),
);

export function isLocalLlmModel(model: string | null | undefined): model is LocalLlmModelSelection {
  return !!model && LOCAL_LLM_MODEL_MAP.has(model);
}

export function getLocalLlmModel(model: string | null | undefined): LocalLlmModelOption | null {
  if (!model) return null;
  return LOCAL_LLM_MODEL_MAP.get(model) ?? null;
}

export function isLocalLlmEnabledPlan(
  plan: string | null | undefined,
): plan is Extract<SubscriptionPlan, "max" | "flex"> {
  return plan === "max" || plan === "flex";
}

export function canUseLocalLlmModel(
  model: string | null | undefined,
  apiKeyMode: ApiKeyMode | string | null | undefined,
  plan: string | null | undefined,
): boolean {
  if (!isLocalLlmModel(model)) return true;
  return apiKeyMode === "platform_credits" && isLocalLlmEnabledPlan(plan);
}

export function getLocalLlmModelEntitlementError(
  model: string | null | undefined,
  apiKeyMode: ApiKeyMode | string | null | undefined,
  plan: string | null | undefined,
): { code: string; message: string; status: number } | null {
  if (!isLocalLlmModel(model)) return null;
  if (apiKeyMode !== "platform_credits") {
    return {
      code: "local_llm_requires_platform_credits",
      message: "Local beta models use platform credits, not BYOK.",
      status: 403,
    };
  }
  if (!isLocalLlmEnabledPlan(plan)) {
    return {
      code: "local_llm_requires_max",
      message: "Local beta models are available on Max and Flex plans.",
      status: 403,
    };
  }
  return null;
}
