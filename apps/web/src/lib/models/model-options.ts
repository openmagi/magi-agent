import { LOCAL_LLM_MODEL_OPTIONS, isLocalLlmEnabledPlan } from "@/lib/models/local-llm";

export interface ModelOption {
  value: string;
  label: string;
}

// "Magi (managed)" — the optional hosted-inference tier for the OSS desktop app.
// Selecting it routes inference through Magi's api-proxy with a subscription
// gateway token instead of a BYO provider key (default model: GLM 5.2). It is a
// routing MODE, not a concrete provider model, so it is provider-agnostic (never
// filtered by key availability) and only surfaced in the local desktop app when
// managed inference is available. See
// docs (clawy): 2026-07-14-oss-magi-managed-inference-subscription-design.md.
export const MAGI_MANAGED_MODEL_VALUE = "magi_managed";

export const MAGI_MANAGED_MODEL_OPTION: ModelOption = {
  value: MAGI_MANAGED_MODEL_VALUE,
  label: "Magi (managed) — no API key",
};

/** True when a model selection is the managed-inference routing mode. */
export function isMagiManagedModel(value: string | null | undefined): boolean {
  return value === MAGI_MANAGED_MODEL_VALUE;
}

// Only concrete provider-backed models the local runtime can serve. Smart-routing
// and Codex are hosted-only — OSS has no smart router backend, so surfacing them
// would let the user pick a model that silently fails. They are deliberately
// excluded here (rather than at the availability filter) so the source list
// itself reflects what the local runtime can run.
export const BASE_MODEL_OPTIONS = [
  { value: "haiku", label: "Claude Haiku 4.5" },
  { value: "sonnet", label: "Claude Sonnet 5" },
  { value: "opus", label: "Claude Opus 4.8" },
  { value: "fable_5", label: "Claude Fable 5" },
  { value: "gpt_5_nano", label: "GPT-5.4 Nano" },
  { value: "gpt_5_mini", label: "GPT-5.4 Mini" },
  { value: "gpt_5_5", label: "GPT-5.5" },
  { value: "gpt_5_5_pro", label: "GPT-5.5 Pro" },
  { value: "kimi_k2_5", label: "Kimi K2.6 (Fireworks AI)" },
  { value: "kimi_k2_7_code", label: "Kimi K2.7 Code (Fireworks AI)" },
  { value: "glm_5_2", label: "GLM 5.2 (Fireworks AI)" },
  { value: "minimax_m2_7", label: "MiniMax M2.7 (Fireworks AI)" },
  { value: "gemini_3_5_flash", label: "Gemini 3.5 Flash (Google)" },
  { value: "gemini_3_1_flash_lite", label: "Gemini 3.1 Flash Lite (Google)" },
  { value: "gemini_3_1_pro", label: "Gemini 3.1 Pro (Google)" },
] as const satisfies readonly ModelOption[];

export function normalizeModelSelectionForSettings(value: string): string {
  if (value === "gpt_5_1") return "gpt_5_mini";
  if (value === "gpt_5_4") return "gpt_5_5";
  return value;
}

export { filterModelOptionsByConfiguredProviders } from "./model-availability";

export interface GetModelOptionsOpts {
  /** When true, surface the "Magi (managed)" hosted-inference option at the top
   * (OSS desktop app with managed inference available). */
  includeManagedInference?: boolean;
}

export function getModelOptions(
  subscriptionPlan: string | null | undefined,
  opts: GetModelOptionsOpts = {},
): ModelOption[] {
  const managedPrefix = opts.includeManagedInference ? [MAGI_MANAGED_MODEL_OPTION] : [];
  const baseOptions = [...managedPrefix, ...BASE_MODEL_OPTIONS];
  if (!isLocalLlmEnabledPlan(subscriptionPlan)) return baseOptions;
  return [
    ...baseOptions,
    ...LOCAL_LLM_MODEL_OPTIONS.map((model) => ({
      value: model.value,
      label: model.label,
    })),
  ];
}
