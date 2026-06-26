// Pure state machine + validation helpers for the OSS onboarding wizard.
//
// Kept free of React/DOM so the multi-step logic (step flow, provider->default
// model, validation, and the config payload) is unit-testable in isolation.
// The wizard component (./onboarding-wizard.tsx) is a thin shell over these.

import {
  LOCAL_RUNTIME_DEFAULT_MODEL,
  type LocalRuntimeProvider,
} from "@/lib/models/local-runtime-models";

/** Minimal fetch surface the wizard needs (injectable for tests). */
export type AgentFetch = (path: string, init?: RequestInit) => Promise<Response>;

/** Provider ids supported by the local CLI resolver (mirrors settings-form). */
export const ALLOWED_PROVIDERS: readonly LocalRuntimeProvider[] = [
  "anthropic",
  "openai",
  "gemini",
  "fireworks",
  "openrouter",
] as const;

/** Human labels per provider (mirrors settings-form PROVIDER_OPTIONS). */
export const PROVIDER_LABELS: Record<LocalRuntimeProvider, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Gemini",
  fireworks: "Fireworks",
  openrouter: "OpenRouter",
};

/** Short "where to get a key" hint per provider. */
const PROVIDER_KEY_HINTS: Record<LocalRuntimeProvider, string> = {
  anthropic: "Get a key at console.anthropic.com (Settings, API keys).",
  openai: "Get a key at platform.openai.com (API keys).",
  gemini: "Get a key at aistudio.google.com (Get API key).",
  fireworks: "Get a key at fireworks.ai (Account, API keys).",
  openrouter: "Get a key at openrouter.ai (Settings, Keys).",
};

/** Ordered wizard steps. Integrations is optional/skippable. */
export const WIZARD_STEPS = ["provider-key", "model", "integrations", "done"] as const;

export type WizardStep = (typeof WIZARD_STEPS)[number];

export interface WizardDraft {
  provider: string;
  apiKey: string;
  model?: string;
  /** True when the model field is free-text ("Custom…") rather than a preset. */
  customModel?: boolean;
}

export function isAllowedProvider(value: string): value is LocalRuntimeProvider {
  return (ALLOWED_PROVIDERS as readonly string[]).includes(value);
}

/**
 * Resolve the provider the wizard should start on from the backend-reported
 * list, narrowed to the providers the local resolver actually supports.
 * Returns `null` when none of the reported ids are supported (empty list or
 * unknown-only), so the caller can render the no-providers escape hatch instead
 * of a locked dropdown.
 */
export function resolveInitialProvider(
  reportedProviders: string[],
): LocalRuntimeProvider | null {
  return reportedProviders.find(isAllowedProvider) ?? null;
}

/**
 * Apply a provider change to the draft: switch provider, reset the model to that
 * provider's default, and clear custom mode. An unsupported `nextProvider` is a
 * no-op (returns the draft unchanged).
 */
export function applyProviderChange(draft: WizardDraft, nextProvider: string): WizardDraft {
  if (!isAllowedProvider(nextProvider)) return draft;
  return {
    ...draft,
    provider: nextProvider,
    model: defaultModelForProvider(nextProvider),
    customModel: false,
  };
}

/**
 * The model id to submit. Preset and custom paths both store the id in
 * `draft.model`, so this returns it directly (the `customModel` flag only drives
 * which input renders).
 */
export function resolveSubmitModel(draft: WizardDraft): string {
  return (draft.model ?? "").trim();
}

export function defaultModelForProvider(provider: LocalRuntimeProvider): string {
  return LOCAL_RUNTIME_DEFAULT_MODEL[provider];
}

export function providerKeyHint(provider: LocalRuntimeProvider): string {
  return PROVIDER_KEY_HINTS[provider];
}

export function nextStep(step: WizardStep): WizardStep {
  const index = WIZARD_STEPS.indexOf(step);
  const next = Math.min(index + 1, WIZARD_STEPS.length - 1);
  return WIZARD_STEPS[next];
}

export function prevStep(step: WizardStep): WizardStep {
  const index = WIZARD_STEPS.indexOf(step);
  const prev = Math.max(index - 1, 0);
  return WIZARD_STEPS[prev];
}

export function validateProviderKeyStep(input: { provider: string; apiKey: string }): boolean {
  return isAllowedProvider(input.provider) && input.apiKey.trim().length > 0;
}

/** Whether the wizard may move forward from `step` given the current draft. */
export function canAdvance(step: WizardStep, draft: WizardDraft): boolean {
  switch (step) {
    case "provider-key":
      return validateProviderKeyStep(draft);
    case "model":
      return (draft.model ?? "").trim().length > 0;
    case "integrations":
    case "done":
      return true;
    default:
      return false;
  }
}

export interface ConfigPayload {
  llm: { provider: string; model: string; apiKey: string };
}

export function buildConfigPayload(input: {
  provider: string;
  model: string;
  apiKey: string;
}): ConfigPayload {
  return {
    llm: {
      provider: input.provider,
      model: input.model,
      apiKey: input.apiKey.trim(),
    },
  };
}

/** Generic fallback message when the server gives no usable error string. */
const GENERIC_SAVE_ERROR = "Failed to save configuration";

export type SubmitResult = { ok: true } | { ok: false; error: string };

/**
 * Persist the wizard draft through the existing `PUT /v1/app/config` endpoint.
 * Pure with respect to React/DOM: `agentFetch` is injected so the network shape
 * and error parsing can be unit-tested directly. Returns a discriminated result
 * the component maps to "clear key + navigate" or "retain key + show error".
 */
export async function submitProviderConfig(
  agentFetch: AgentFetch,
  draft: WizardDraft,
): Promise<SubmitResult> {
  try {
    const res = await agentFetch("/v1/app/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        buildConfigPayload({
          provider: draft.provider,
          model: resolveSubmitModel(draft),
          apiKey: draft.apiKey,
        }),
      ),
    });
    if (res.ok) return { ok: true };
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    return { ok: false, error: data.error || GENERIC_SAVE_ERROR };
  } catch {
    return { ok: false, error: GENERIC_SAVE_ERROR };
  }
}
