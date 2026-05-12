/**
 * Model capability registry (T4-17).
 *
 * Single source of truth for per-model attributes the runtime needs:
 *   - supportsThinking  → gate `thinking: {type: "adaptive"}` so we
 *                          don't send it to models that reject / ignore
 *                          it (e.g. Haiku 4.5).
 *   - maxOutputTokens   → Anthropic's per-model output cap. Used by
 *                          callers that want to align `max_tokens`.
 *   - contextWindow     → prompt + completion capacity. Used by
 *                          Turn.ts to size the compaction threshold
 *                          per model instead of the hard-coded 150k.
 *   - inputUsdPerMtok /
 *     outputUsdPerMtok  → pricing previously in pricing.ts (T1-06).
 *
 * Numbers mirror Anthropic's public list pricing (2026-04, USD):
 *   - Opus 4.x   → $15 in / $75 out per million tokens
 *   - Sonnet 4.x → $3  in / $15 out
 *   - Haiku 4.x  → $1  in / $5  out
 *   - GPT-5.4 Nano → $0.20 in / $1.25 out
 *   - GPT-5.4 Mini → $0.75 in / $4.50 out
 *   - GPT-5.5      → $5    in / $30 out
 *   - GPT-5.5 Pro  → $30   in / $180 out
 *
 * Unknown models fail-open: `getCapability` returns null, `computeUsd`
 * returns 0, `shouldEnableThinkingByDefault` returns false. An unknown
 * model is warned ONCE per process so operators notice missing entries
 * without spamming the per-turn hot path.
 */

import {
  ModelRegistry,
  defaultModelRegistryPath,
  isModelRegistryEnabled,
  type ModelCapability as RegistryModelCapability,
} from "../config/ModelRegistry.js";

export interface ModelCapability {
  /** Model id as used by Anthropic /v1/messages. */
  id: string;
  /** Does the model support extended thinking blocks? */
  supportsThinking: boolean;
  /** Max output tokens per response (Anthropic per-model limit). */
  maxOutputTokens: number;
  /** Context window (prompt + completion combined). */
  contextWindow: number;
  /** USD per million input tokens. */
  inputUsdPerMtok: number;
  /** USD per million output tokens. */
  outputUsdPerMtok: number;
}

export const MODEL_CAPABILITIES: Record<string, ModelCapability> = {
  "claude-opus-4-7": {
    id: "claude-opus-4-7",
    supportsThinking: true,
    maxOutputTokens: 32_000,
    contextWindow: 900_000,
    inputUsdPerMtok: 15,
    outputUsdPerMtok: 75,
  },
  "claude-opus-4-6": {
    id: "claude-opus-4-6",
    supportsThinking: true,
    maxOutputTokens: 32_000,
    contextWindow: 200_000,
    inputUsdPerMtok: 15,
    outputUsdPerMtok: 75,
  },
  "claude-sonnet-4-6": {
    id: "claude-sonnet-4-6",
    supportsThinking: true,
    maxOutputTokens: 16_000,
    contextWindow: 200_000,
    inputUsdPerMtok: 3,
    outputUsdPerMtok: 15,
  },
  "claude-haiku-4-5-20251001": {
    id: "claude-haiku-4-5-20251001",
    supportsThinking: false,
    maxOutputTokens: 8_000,
    contextWindow: 200_000,
    inputUsdPerMtok: 1,
    outputUsdPerMtok: 5,
  },
  "claude-haiku-4-5": {
    id: "claude-haiku-4-5",
    supportsThinking: false,
    maxOutputTokens: 8_000,
    contextWindow: 200_000,
    inputUsdPerMtok: 1,
    outputUsdPerMtok: 5,
  },
  "anthropic/claude-opus-4-7": {
    id: "claude-opus-4-7",
    supportsThinking: true,
    maxOutputTokens: 32_000,
    contextWindow: 900_000,
    inputUsdPerMtok: 15,
    outputUsdPerMtok: 75,
  },
  "anthropic/claude-opus-4-6": {
    id: "claude-opus-4-6",
    supportsThinking: true,
    maxOutputTokens: 32_000,
    contextWindow: 200_000,
    inputUsdPerMtok: 15,
    outputUsdPerMtok: 75,
  },
  "anthropic/claude-sonnet-4-6": {
    id: "claude-sonnet-4-6",
    supportsThinking: true,
    maxOutputTokens: 16_000,
    contextWindow: 200_000,
    inputUsdPerMtok: 3,
    outputUsdPerMtok: 15,
  },
  "anthropic/claude-haiku-4-5": {
    id: "claude-haiku-4-5",
    supportsThinking: false,
    maxOutputTokens: 8_000,
    contextWindow: 200_000,
    inputUsdPerMtok: 1,
    outputUsdPerMtok: 5,
  },
  "openai/gpt-5.4-nano": {
    id: "openai/gpt-5.4-nano",
    supportsThinking: false,
    maxOutputTokens: 4_096,
    contextWindow: 128_000,
    inputUsdPerMtok: 0.2,
    outputUsdPerMtok: 1.25,
  },
  "gpt-5.4-nano": {
    id: "gpt-5.4-nano",
    supportsThinking: false,
    maxOutputTokens: 4_096,
    contextWindow: 128_000,
    inputUsdPerMtok: 0.2,
    outputUsdPerMtok: 1.25,
  },
  "gpt-5-nano": {
    id: "gpt-5-nano",
    supportsThinking: false,
    maxOutputTokens: 8_000,
    contextWindow: 400_000,
    inputUsdPerMtok: 0.05,
    outputUsdPerMtok: 0.4,
  },
  "gpt-5-mini": {
    id: "gpt-5-mini",
    supportsThinking: false,
    maxOutputTokens: 16_000,
    contextWindow: 400_000,
    inputUsdPerMtok: 0.25,
    outputUsdPerMtok: 2,
  },
  "gpt-5.1": {
    id: "gpt-5.1",
    supportsThinking: false,
    maxOutputTokens: 16_000,
    contextWindow: 400_000,
    inputUsdPerMtok: 1.25,
    outputUsdPerMtok: 10,
  },
  "gpt-5.4": {
    id: "gpt-5.4",
    supportsThinking: false,
    maxOutputTokens: 32_000,
    contextWindow: 400_000,
    inputUsdPerMtok: 2.5,
    outputUsdPerMtok: 15,
  },
  "openai/gpt-5.4-mini": {
    id: "openai/gpt-5.4-mini",
    supportsThinking: false,
    maxOutputTokens: 8_192,
    contextWindow: 128_000,
    inputUsdPerMtok: 0.75,
    outputUsdPerMtok: 4.5,
  },
  "gpt-5.4-mini": {
    id: "gpt-5.4-mini",
    supportsThinking: false,
    maxOutputTokens: 8_192,
    contextWindow: 128_000,
    inputUsdPerMtok: 0.75,
    outputUsdPerMtok: 4.5,
  },
  "openai/gpt-5.5": {
    id: "openai/gpt-5.5",
    supportsThinking: false,
    maxOutputTokens: 128_000,
    contextWindow: 1_000_000,
    inputUsdPerMtok: 5,
    outputUsdPerMtok: 30,
  },
  "gpt-5.5": {
    id: "gpt-5.5",
    supportsThinking: false,
    maxOutputTokens: 128_000,
    contextWindow: 1_000_000,
    inputUsdPerMtok: 5,
    outputUsdPerMtok: 30,
  },
  "openai/gpt-5.5-pro": {
    id: "openai/gpt-5.5-pro",
    supportsThinking: false,
    maxOutputTokens: 128_000,
    contextWindow: 1_050_000,
    inputUsdPerMtok: 30,
    outputUsdPerMtok: 180,
  },
  "openai-codex/gpt-5.5": {
    id: "openai-codex/gpt-5.5",
    supportsThinking: false,
    maxOutputTokens: 128_000,
    contextWindow: 1_000_000,
    inputUsdPerMtok: 5,
    outputUsdPerMtok: 30,
  },
  "fireworks/kimi-k2p6": {
    id: "fireworks/kimi-k2p6",
    supportsThinking: false,
    maxOutputTokens: 32_768,
    contextWindow: 262_144,
    inputUsdPerMtok: 0.95,
    outputUsdPerMtok: 4,
  },
  "kimi-k2p6": {
    id: "kimi-k2p6",
    supportsThinking: false,
    maxOutputTokens: 32_000,
    contextWindow: 256_000,
    inputUsdPerMtok: 0.95,
    outputUsdPerMtok: 4,
  },
  "fireworks/minimax-m2p7": {
    id: "fireworks/minimax-m2p7",
    supportsThinking: false,
    maxOutputTokens: 8_192,
    contextWindow: 196_608,
    inputUsdPerMtok: 0.3,
    outputUsdPerMtok: 1.2,
  },
  "minimax-m2p7": {
    id: "minimax-m2p7",
    supportsThinking: false,
    maxOutputTokens: 16_000,
    contextWindow: 256_000,
    inputUsdPerMtok: 0.3,
    outputUsdPerMtok: 1.2,
  },
  "google/gemini-3.1-flash-lite-preview": {
    id: "google/gemini-3.1-flash-lite-preview",
    supportsThinking: false,
    maxOutputTokens: 65_536,
    contextWindow: 1_048_576,
    inputUsdPerMtok: 0.25,
    outputUsdPerMtok: 1.5,
  },
  "gemini-3.1-flash-lite-preview": {
    id: "gemini-3.1-flash-lite-preview",
    supportsThinking: false,
    maxOutputTokens: 16_000,
    contextWindow: 1_000_000,
    inputUsdPerMtok: 0.25,
    outputUsdPerMtok: 1.5,
  },
  "google/gemini-3.1-pro-preview": {
    id: "google/gemini-3.1-pro-preview",
    supportsThinking: false,
    maxOutputTokens: 65_536,
    contextWindow: 1_048_576,
    inputUsdPerMtok: 2,
    outputUsdPerMtok: 12,
  },
  "gemini-3.1-pro-preview": {
    id: "gemini-3.1-pro-preview",
    supportsThinking: false,
    maxOutputTokens: 32_000,
    contextWindow: 1_000_000,
    inputUsdPerMtok: 2,
    outputUsdPerMtok: 12,
  },
  "local/gemma-fast": {
    id: "local/gemma-fast",
    supportsThinking: false,
    maxOutputTokens: 8_192,
    contextWindow: 131_072,
    inputUsdPerMtok: 0,
    outputUsdPerMtok: 0,
  },
  "local/gemma-max": {
    id: "local/gemma-max",
    supportsThinking: false,
    maxOutputTokens: 8_192,
    contextWindow: 131_072,
    inputUsdPerMtok: 0,
    outputUsdPerMtok: 0,
  },
  "local/qwen-uncensored": {
    id: "local/qwen-uncensored",
    supportsThinking: false,
    maxOutputTokens: 8_192,
    contextWindow: 131_072,
    inputUsdPerMtok: 0,
    outputUsdPerMtok: 0,
  },
};

const CUSTOM_MODEL_CAPABILITIES: Record<string, ModelCapability> = {};

export function registerModelCapability(capability: ModelCapability): void {
  CUSTOM_MODEL_CAPABILITIES[capability.id] = capability;
}

export function resetCustomModelCapabilitiesForTests(): void {
  for (const key of Object.keys(CUSTOM_MODEL_CAPABILITIES)) {
    delete CUSTOM_MODEL_CAPABILITIES[key];
  }
}

export function getRegisteredCapability(model: string): ModelCapability | null {
  return CUSTOM_MODEL_CAPABILITIES[model] ?? MODEL_CAPABILITIES[model] ?? null;
}

/**
 * Return the capability record for a model id, or null if unknown.
 * Unknown models are warned once per process so operators can add
 * missing entries without noise.
 */
export function getCapability(model: string): ModelCapability | null {
  const registryCap = getRegistryCapability(model);
  if (registryCap) return registryCap;

  const cap = getRegisteredCapability(model) ?? deriveOpenAICompatibleCapability(model);
  if (!cap) {
    warnUnknownModelOnce(model);
    return null;
  }
  return cap;
}

/**
 * Compute USD cost for an LLM round-trip given the model id and token
 * counts. Returns 0 for unknown models (fail-open).
 */
export function computeUsd(
  model: string,
  inputTokens: number,
  outputTokens: number,
): number {
  const cap = getCapability(model);
  if (!cap) return 0;
  return (
    (inputTokens / 1_000_000) * cap.inputUsdPerMtok +
    (outputTokens / 1_000_000) * cap.outputUsdPerMtok
  );
}

/**
 * Whether a caller should send `thinking: {type: "adaptive"}` for
 * the given model by default. Returns false for unknown models so we
 * never send `thinking` to a model that might 400 on it.
 */
export function shouldEnableThinkingByDefault(model: string): boolean {
  const cap =
    getRegistryCapability(model) ??
    CUSTOM_MODEL_CAPABILITIES[model] ??
    MODEL_CAPABILITIES[model] ??
    deriveOpenAICompatibleCapability(model);
  return cap?.supportsThinking ?? false;
}

/**
 * Conservative fallback when the caller passes an unknown model id.
 * Matches the Sonnet/Haiku 4.x family's 200k window — small enough
 * that unknown models get realistic compaction pressure, large enough
 * that the turn never trips the impossible-budget gate for a
 * well-behaved server.
 */
export const DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000;

/**
 * Resolve the context window for a model id, falling back to
 * DEFAULT_CONTEXT_WINDOW_TOKENS when the id is not registered. Used by
 * ContextEngine (§11.6) so the compaction reserve floor can be capped
 * to the model's real window without needing a hard dependency on
 * `getCapability` at every call site.
 */
export function getContextWindowOrDefault(model: string): number {
  const cap =
    getRegistryCapability(model) ??
    CUSTOM_MODEL_CAPABILITIES[model] ??
    MODEL_CAPABILITIES[model] ??
    deriveOpenAICompatibleCapability(model);
  return cap?.contextWindow ?? DEFAULT_CONTEXT_WINDOW_TOKENS;
}

export function getDefaultTemperature(model: string): number | undefined {
  return getActiveModelRegistry()?.getModel(model)?.temperature;
}

export function getModelProviderParams(model: string): Record<string, unknown> {
  return getActiveModelRegistry()?.getModel(model)?.providerParams ?? {};
}

export function getModelEditFormat(model: string): string | undefined {
  return getActiveModelRegistry()?.getModel(model)?.editFormat;
}

const OPENAI_COMPAT_PREFIXES = [
  "ollama/",
  "local/",
  "localai/",
  "vllm/",
  "tgi/",
  "custom/",
  "openrouter/",
];

function deriveOpenAICompatibleCapability(model: string): ModelCapability | null {
  if (!OPENAI_COMPAT_PREFIXES.some((prefix) => model.startsWith(prefix))) {
    return null;
  }
  return {
    id: model,
    supportsThinking: false,
    maxOutputTokens: 8_192,
    contextWindow: 131_072,
    inputUsdPerMtok: 0,
    outputUsdPerMtok: 0,
  };
}

let activeRegistry: ModelRegistry | null = null;

function getActiveModelRegistry(): ModelRegistry | null {
  if (!isModelRegistryEnabled()) {
    if (activeRegistry) {
      activeRegistry.close();
      activeRegistry = null;
    }
    return null;
  }
  if (!activeRegistry || activeRegistry.path !== defaultModelRegistryPath()) {
    activeRegistry?.close();
    activeRegistry = new ModelRegistry({ path: defaultModelRegistryPath(), watch: true });
  }
  return activeRegistry;
}

function getRegistryCapability(model: string): ModelCapability | null {
  const registryModel = getActiveModelRegistry()?.getModel(model);
  return registryModel ? toRuntimeCapability(registryModel) : null;
}

function toRuntimeCapability(model: RegistryModelCapability): ModelCapability {
  return {
    id: model.id,
    supportsThinking:
      (model.thinking !== undefined && model.thinking.type !== "none") ||
      model.capabilities.includes("extended_thinking"),
    maxOutputTokens: model.maxOutput,
    contextWindow: model.contextWindow,
    inputUsdPerMtok: model.pricing.inputPerMtok,
    outputUsdPerMtok: model.pricing.outputPerMtok,
  };
}

export function reloadModelRegistryForTests(): void {
  activeRegistry?.close();
  activeRegistry = null;
  if (isModelRegistryEnabled()) {
    activeRegistry = new ModelRegistry({ path: defaultModelRegistryPath(), watch: false });
  }
}

const warnedModels = new Set<string>();
function warnUnknownModelOnce(model: string): void {
  if (warnedModels.has(model)) return;
  warnedModels.add(model);
  console.warn(
    `[modelCapabilities] unknown model "${model}" — costUsd=0, thinking=off, contextWindow=default. Add it to config/model-registry.yaml or MODEL_CAPABILITIES in src/llm/modelCapabilities.ts.`,
  );
}
