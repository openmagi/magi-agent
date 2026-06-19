// Curated model presets for the self-hosted Local Runtime settings, per provider.
//
// Labels mirror the hosted model catalog (src/lib/constants.ts MODEL_LABELS /
// model-options.ts) so the names match what users see elsewhere. VALUES are the
// raw model ids the LOCAL CLI resolver feeds to LiteLlm — i.e. the bare model
// without the `<provider>/` litellm prefix (see magi_agent/cli/providers.py:
// the prefix is applied by ProviderConfig.litellm_model).
//
// Fireworks ids: the bare model name (e.g. `kimi-k2p6`) is what the runtime
// resolver expects and matches `_DEFAULT_MODEL["fireworks"]`; LiteLLM then
// dispatches `fireworks_ai/kimi-k2p6` to the Fireworks endpoint. The legacy
// `accounts/fireworks/models/kimi-k2-instruct` id was retired from Fireworks'
// catalog, so it is deliberately not offered.
//
// Model ids drift; these are a best-effort starting point. The Settings form
// always offers a "Custom…" option so any id the provider supports can be typed.

export type LocalRuntimeProvider = "anthropic" | "openai" | "gemini" | "fireworks" | "openrouter";

export interface LocalRuntimeModelOption {
  value: string;
  label: string;
}

/** Sentinel select value that reveals the free-text model input. */
export const CUSTOM_MODEL_VALUE = "__custom__";

export const LOCAL_RUNTIME_MODEL_PRESETS: Record<
  LocalRuntimeProvider,
  readonly LocalRuntimeModelOption[]
> = {
  anthropic: [
    { value: "claude-opus-4-8", label: "Claude Opus 4.8" },
    { value: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
    { value: "claude-haiku-4-5", label: "Claude Haiku 4.5" },
    // Backward-compat: keep the prior Opus id selectable so configs that
    // already saved it don't become "unknown model".
    { value: "claude-opus-4-6", label: "Claude Opus 4.6 (legacy)" },
  ],
  openai: [
    { value: "gpt-5.5", label: "GPT-5.5" },
    { value: "gpt-5.5-pro", label: "GPT-5.5 Pro" },
    { value: "gpt-5.4-mini", label: "GPT-5.4 Mini" },
    { value: "gpt-5.4-nano", label: "GPT-5.4 Nano" },
  ],
  gemini: [
    { value: "gemini-3.5-flash", label: "Gemini 3.5 Flash" },
    { value: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro (preview)" },
    { value: "gemini-3.1-flash-lite-preview", label: "Gemini 3.1 Flash Lite (preview)" },
  ],
  fireworks: [
    { value: "kimi-k2p6", label: "Kimi K2.6" },
    { value: "kimi-k2p5", label: "Kimi K2.5" },
    { value: "minimax-m2p7", label: "MiniMax M2.7" },
  ],
  openrouter: [
    { value: "openai/gpt-5.5", label: "GPT-5.5 (via OpenRouter)" },
    { value: "anthropic/claude-sonnet-4-6", label: "Claude Sonnet 4.6 (via OpenRouter)" },
  ],
};

/** Per-provider default model (mirrors magi_agent/cli/providers.py _DEFAULT_MODEL). */
export const LOCAL_RUNTIME_DEFAULT_MODEL: Record<LocalRuntimeProvider, string> = {
  anthropic: "claude-sonnet-4-6",
  openai: "gpt-5.5",
  gemini: "gemini-3.5-flash",
  fireworks: "kimi-k2p6",
  openrouter: "openai/gpt-5.5",
};

export function isPresetModel(provider: LocalRuntimeProvider, model: string): boolean {
  return LOCAL_RUNTIME_MODEL_PRESETS[provider].some((option) => option.value === model);
}
