"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useOptionalMessages } from "@/lib/i18n";
import { useAgentFetch } from "@/lib/local-api";
import {
  CUSTOM_MODEL_VALUE,
  LOCAL_RUNTIME_MODEL_PRESETS,
} from "@/lib/models/local-runtime-models";
import {
  PROVIDER_LABELS,
  applyProviderChange,
  canAdvance,
  defaultModelForProvider,
  isAllowedProvider,
  nextStep,
  prevStep,
  providerKeyHint,
  resolveInitialProvider,
  submitProviderConfig,
  type WizardDraft,
  type WizardStep,
} from "./wizard-state";
import type { LocalRuntimeProvider } from "@/lib/models/local-runtime-models";

/** Static target routes for the OSS local dashboard (botId is "local"). */
const CHAT_ROUTE = "/dashboard/local/chat/general";
const INTEGRATIONS_ROUTE = "/dashboard/local/integrations";

export interface OnboardingWizardProps {
  open: boolean;
  /** Provider ids from `bootstrap.setup.providers` (backend-driven). */
  providers: string[];
  onClose: () => void;
}

export function OnboardingWizard({
  open,
  providers,
  onClose,
}: OnboardingWizardProps): React.ReactElement | null {
  const agentFetch = useAgentFetch();
  const t = useOptionalMessages();
  const copy = t.onboarding;

  // Provider options stay backend-driven: only ids the backend reported and that
  // the local resolver supports are offered (label from the shared map).
  const providerOptions = useMemo(
    () =>
      providers
        .filter(isAllowedProvider)
        .map((id) => ({ value: id, label: PROVIDER_LABELS[id] })),
    [providers],
  );

  // Derive the starting provider from the FILTERED list. `null` means none of the
  // reported providers are supported -> we render the no-providers escape hatch.
  const initialProvider = useMemo(() => resolveInitialProvider(providers), [providers]);

  const [step, setStep] = useState<WizardStep>("provider-key");
  const [provider, setProvider] = useState<LocalRuntimeProvider>(initialProvider ?? "anthropic");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(defaultModelForProvider(initialProvider ?? "anthropic"));
  const [customModel, setCustomModel] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Body scroll-lock while the overlay is mounted, matching the shared Modal.
  // This runs only in an effect, so it is a no-op during SSR / server snapshots.
  // Deliberate divergence from Modal: no Esc / no backdrop-click close. The only
  // escape hatch is the explicit Skip link below.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  if (!open) return null;

  const draft: WizardDraft = { provider, apiKey, model, customModel };
  const advanceDisabled = !canAdvance(step, draft);
  const hasProviders = initialProvider !== null;

  function handleProviderChange(next: string): void {
    if (!isAllowedProvider(next)) return;
    const updated = applyProviderChange(draft, next);
    setProvider(next);
    setModel(updated.model ?? "");
    setCustomModel(false);
  }

  function handleModelChange(next: string): void {
    if (next === CUSTOM_MODEL_VALUE) {
      setCustomModel(true);
      // Start the free-text field empty rather than pre-filled with the preset id.
      setModel("");
      return;
    }
    setCustomModel(false);
    setModel(next);
  }

  // Persist the provider/model/key through the existing config endpoint, then
  // navigate. `destination` lets "Finish" land on chat and "Connect tools" land
  // on the integrations page without losing the just-entered key. The submit +
  // error parsing live in the tested pure `submitProviderConfig`.
  async function saveAndGo(destination: string): Promise<void> {
    setSaving(true);
    setError(null);
    const result = await submitProviderConfig(agentFetch, draft);
    if (result.ok) {
      // Drop the key from memory; never echo it again.
      setApiKey("");
      setSaving(false);
      onClose();
      if (typeof window !== "undefined") {
        window.location.assign(destination);
      }
      return;
    }
    // Keep the key so the user can retry; surface the error.
    setError(result.error);
    setSaving(false);
  }

  const stepNumber = step === "provider-key" ? 1 : step === "model" ? 2 : 3;
  const stepLabel = copy.localSetupStep
    .replace("{current}", String(stepNumber))
    .replace("{total}", "3");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-md">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={copy.localSetupTitle}
        className="w-full max-w-lg overflow-y-auto rounded-2xl border border-black/10 bg-white p-6 shadow-xl max-h-[85vh]"
      >
        <header className="mb-4">
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-secondary/60">
            {stepLabel}
          </div>
          <h2 className="mt-1 text-lg font-semibold text-foreground">{copy.localSetupTitle}</h2>
          <p className="mt-1 text-sm text-secondary">{copy.localSetupSubtitle}</p>
        </header>

        {error ? (
          <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-500">
            {error}
          </div>
        ) : null}

        {!hasProviders ? (
          <div className="rounded-lg border border-black/[0.06] bg-black/[0.02] px-3 py-3 text-sm text-secondary">
            {copy.localSetupNoProviders}
          </div>
        ) : null}

        {hasProviders && step === "provider-key" ? (
          <div className="space-y-3">
            <Select
              label={copy.localSetupProviderLabel}
              value={provider}
              options={providerOptions}
              onChange={handleProviderChange}
            />
            <div>
              <Input
                label={copy.localSetupApiKeyLabel}
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="sk-..."
              />
              <p className="mt-1 text-xs text-muted">{providerKeyHint(provider)}</p>
            </div>
          </div>
        ) : null}

        {hasProviders && step === "model" ? (
          <div className="space-y-3">
            <Select
              label={copy.localSetupModelLabel}
              value={customModel ? CUSTOM_MODEL_VALUE : model}
              options={[
                ...LOCAL_RUNTIME_MODEL_PRESETS[provider],
                { value: CUSTOM_MODEL_VALUE, label: copy.localSetupCustomModelOption },
              ]}
              onChange={handleModelChange}
            />
            {customModel ? (
              <Input
                label={copy.localSetupCustomModelLabel}
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder="claude-sonnet-4-6, gpt-5.5, accounts/fireworks/models/…"
              />
            ) : null}
          </div>
        ) : null}

        {hasProviders && step === "integrations" ? (
          <div className="space-y-3">
            <p className="text-sm text-secondary">{copy.localSetupIntegrationsCopy}</p>
            <Button
              variant="secondary"
              size="md"
              onClick={() => void saveAndGo(INTEGRATIONS_ROUTE)}
              disabled={saving}
            >
              {copy.localSetupConnectTools}
            </Button>
          </div>
        ) : null}

        <div className="mt-6 flex items-center justify-between gap-3 border-t border-black/[0.06] pt-4">
          <button
            type="button"
            onClick={onClose}
            className="text-xs font-medium text-muted underline-offset-2 hover:underline"
          >
            {copy.localSetupSkip}
          </button>

          {hasProviders ? (
            <div className="flex items-center gap-2">
              {step !== "provider-key" ? (
                <Button
                  variant="secondary"
                  size="md"
                  onClick={() => setStep(prevStep(step))}
                  disabled={saving}
                >
                  {copy.localSetupBack}
                </Button>
              ) : null}
              {step === "integrations" ? (
                <Button
                  variant="cta"
                  size="md"
                  onClick={() => void saveAndGo(CHAT_ROUTE)}
                  disabled={saving}
                >
                  {saving ? copy.localSetupSaving : copy.localSetupFinish}
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="md"
                  onClick={() => setStep(nextStep(step))}
                  disabled={advanceDisabled || saving}
                >
                  {copy.localSetupNext}
                </Button>
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
