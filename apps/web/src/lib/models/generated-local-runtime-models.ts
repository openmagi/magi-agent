// DO NOT EDIT — generated from magi_agent/models/builtin_catalog.json
// Re-run: python -m magi_agent.models.export_ts \
//   --out apps/web/src/lib/models/generated-local-runtime-models.ts

export type LocalRuntimeProvider =
  | "anthropic"
  | "openai"
  | "gemini"
  | "fireworks"
  | "openrouter";

export interface LocalRuntimeModelOption {
  value: string;
  label: string;
}

/** Per-provider preset list (catalog source=direct or router). */
export const GENERATED_LOCAL_RUNTIME_MODEL_PRESETS: Record<
  LocalRuntimeProvider,
  readonly LocalRuntimeModelOption[]
> = {
  anthropic: [
    { value: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
    { value: "claude-opus-4-8", label: "Claude Opus 4.8" },
    { value: "claude-haiku-4-5", label: "Claude Haiku 4.5" },
    { value: "haiku", label: "Claude Haiku (alias)" },
    { value: "claude-opus-4-6", label: "Claude Opus 4.6 (legacy)" },  // deprecated (kept for backward compat)
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

/** Per-provider default model id, sourced from the catalog. */
export const GENERATED_LOCAL_RUNTIME_DEFAULT_MODEL: Record<LocalRuntimeProvider, string> = {
  anthropic: "claude-sonnet-4-6",
  openai: "gpt-5.5",
  gemini: "gemini-3.5-flash",
  fireworks: "kimi-k2p6",
  openrouter: "openai/gpt-5.5",
};
