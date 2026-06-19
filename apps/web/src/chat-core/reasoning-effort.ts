// Cross-provider "reasoning effort" knob. The literal values match the
// runtime's litellm wrapper (see magi_agent/cli/real_runner.py
// `_normalize_reasoning_effort`): the wrapper accepts these and rewrites them
// per provider (notably `max` → `xhigh` for OpenAI/OpenRouter, `max` → `high`
// for Gemini). Frontend uses the user-facing 4 levels; "minimal" stays a
// runtime-only escape hatch.

export type ReasoningEffort = "minimal" | "low" | "medium" | "high";

export const REASONING_EFFORT_VALUES: readonly ReasoningEffort[] = [
  "minimal",
  "low",
  "medium",
  "high",
] as const;

export const DEFAULT_REASONING_EFFORT: ReasoningEffort = "medium";

/**
 * Map a runtime model id (the string `channelModelSelectionToRuntimeModel`
 * returns, e.g. "anthropic/claude-opus-4-8", "openai/gpt-5.5",
 * "google/gemini-3.1-pro-preview") to the provider whose reasoning support we
 * key off. Returns null when the model is not one we can confidently classify
 * (custom ids, local models, empty sentinel — UI should hide the control).
 */
export function modelProviderForReasoning(runtimeModel: string): string | null {
  if (!runtimeModel) return null;
  const slash = runtimeModel.indexOf("/");
  if (slash <= 0) return null;
  const head = runtimeModel.slice(0, slash).toLowerCase();
  // Normalize provider aliases used in MODEL_SELECTION_TO_RUNTIME_MODEL.
  if (head === "google") return "gemini";
  if (head === "openai-codex") return "openai";
  return head;
}

/**
 * Does the model support a reasoning-effort knob? Anthropic (extended
 * thinking), OpenAI (o-series/GPT-5 reasoning_effort), and Gemini (thinking)
 * all do. Fireworks-hosted open models (Kimi, MiniMax) and local runtimes
 * don't expose a comparable knob via litellm, so the UI hides it for them.
 */
export function modelSupportsReasoningEffort(runtimeModel: string): boolean {
  const provider = modelProviderForReasoning(runtimeModel);
  if (!provider) return false;
  return provider === "anthropic" || provider === "openai" || provider === "gemini";
}
