// Key-aware filtering for chat model-picker options. Kept import-free (no `@/`
// path aliases, no framework) so it is unit-testable in isolation.

export interface ModelOptionLike {
  value: string;
  label: string;
}

// Map each model-selection option value to the provider whose API key it needs.
// Provider-agnostic options (e.g. router tokens persisted by chat-core) are
// intentionally ABSENT — they are never filtered out by key availability and
// will be normalised by the picker fallback labels.
const OPTION_PROVIDER: Record<string, string> = {
  haiku: "anthropic",
  sonnet: "anthropic",
  opus: "anthropic",
  gpt_5_nano: "openai",
  gpt_5_mini: "openai",
  gpt_5_5: "openai",
  gpt_5_5_pro: "openai",
  kimi_k2_5: "fireworks",
  minimax_m2_7: "fireworks",
  gemini_3_1_flash_lite: "gemini",
  gemini_3_1_pro: "gemini",
};

/**
 * Filter chat-picker model options to providers that actually have a configured
 * API key, so a local bot never advertises a model it cannot run (which would
 * otherwise fail silently with an empty response). Mirrors the key-aware
 * subagent routing on the runtime side.
 *
 * - `configuredProviders` is the set of provider names with a usable key
 *   (the dashboard `GET /v1/app/providers` `configured` flags). The `google`
 *   alias is treated as `gemini`.
 * - Provider-agnostic options (router tiers, local models — absent from
 *   `OPTION_PROVIDER`) are always kept.
 * - Fail-open: an empty set returns the options unchanged.
 * - The currently-selected value is always kept so the picker can still show it.
 */
export function filterModelOptionsByConfiguredProviders<T extends ModelOptionLike>(
  options: T[],
  configuredProviders: ReadonlySet<string>,
  selectedValue?: string,
): T[] {
  if (configuredProviders.size === 0) return options;
  const keyed = new Set(
    [...configuredProviders].map((p) => (p === "google" ? "gemini" : p)),
  );
  return options.filter((option) => {
    if (option.value === selectedValue) return true;
    const provider = OPTION_PROVIDER[option.value];
    if (provider === undefined) return true; // provider-agnostic (router/local)
    return keyed.has(provider);
  });
}
