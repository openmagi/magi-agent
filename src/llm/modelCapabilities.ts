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
 *
 * Unknown models fail-open: `getCapability` returns null, `computeUsd`
 * returns 0, `shouldEnableThinkingByDefault` returns false. An unknown
 * model is warned ONCE per process so operators notice missing entries
 * without spamming the per-turn hot path.
 */

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
};

/**
 * Return the capability record for a model id, or null if unknown.
 * Unknown models are warned once per process so operators can add
 * missing entries without noise.
 */
export function getCapability(model: string): ModelCapability | null {
  const cap = MODEL_CAPABILITIES[model];
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
  const cap = MODEL_CAPABILITIES[model];
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
  const cap = MODEL_CAPABILITIES[model];
  return cap?.contextWindow ?? DEFAULT_CONTEXT_WINDOW_TOKENS;
}

const warnedModels = new Set<string>();
function warnUnknownModelOnce(model: string): void {
  if (warnedModels.has(model)) return;
  warnedModels.add(model);
  console.warn(
    `[modelCapabilities] unknown model "${model}" — costUsd=0, thinking=off, contextWindow=default. Add to MODEL_CAPABILITIES in src/llm/modelCapabilities.ts.`,
  );
}
