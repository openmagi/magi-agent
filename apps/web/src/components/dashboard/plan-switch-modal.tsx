"use client";

import { useState } from "react";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useMessages } from "@/lib/i18n";
import { isUpgrade as isPlanUpgrade, isFireworksModel } from "@/lib/billing/plans";
import type { SubscriptionInfo } from "./plan-switch-card";

export type PlanType = "byok" | "pro" | "pro_plus" | "max" | "flex";

const PLAN_LABELS: Record<PlanType, string> = {
  byok: "BYOK",
  pro: "Pro",
  pro_plus: "Pro+",
  max: "MAX",
  flex: "FLEX",
};

const PLAN_PRICES: Record<PlanType, string> = {
  byok: "$7.99/mo",
  pro: "$14.99/mo",
  pro_plus: "$89.99/mo",
  max: "$399/mo",
  flex: "$1,999/mo",
};

interface PlanSwitchModalProps {
  open: boolean;
  onClose: () => void;
  subscription: SubscriptionInfo;
  targetPlan: PlanType;
  currentModel?: string;
  onConfirm: (opts: {
    targetPlan: PlanType;
    anthropicApiKey?: string;
    fireworksApiKey?: string;
  }) => Promise<void>;
}

export function PlanSwitchModal({
  open,
  onClose,
  subscription,
  targetPlan,
  currentModel,
  onConfirm,
}: PlanSwitchModalProps) {
  const t = useMessages();
  const upgrading = isPlanUpgrade(subscription.plan, targetPlan);
  const needsApiKey = targetPlan === "byok";
  const modelIsFireworks = isFireworksModel(currentModel ?? "");

  const [apiKey, setApiKey] = useState("");
  const [fireworksKey, setFireworksKey] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm() {
    if (needsApiKey) {
      const requiredKey = modelIsFireworks ? fireworksKey.trim() : apiKey.trim();
      if (!requiredKey) {
        setError(modelIsFireworks ? t.planSwitch.fireworksApiKeyRequired : t.planSwitch.apiKeyRequired);
        return;
      }
    }

    setSubmitting(true);
    setError(null);

    try {
      await onConfirm({
        targetPlan,
        anthropicApiKey: needsApiKey ? apiKey.trim() || undefined : undefined,
        fireworksApiKey: needsApiKey ? fireworksKey.trim() || undefined : undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t.planSwitch.switchFailed);
    } finally {
      setSubmitting(false);
    }
  }

  function handleClose() {
    if (!submitting) {
      setError(null);
      setApiKey("");
      setFireworksKey("");
      setShowAdvanced(false);
      onClose();
    }
  }

  const targetLabel = PLAN_LABELS[targetPlan] ?? targetPlan;
  const targetPrice = PLAN_PRICES[targetPlan] ?? "";

  return (
    <Modal open={open} onClose={handleClose}>
      <div className="p-6">
        <h2 className="text-xl font-bold text-foreground mb-2">
          {t.planSwitch.switchTo} {targetLabel}
        </h2>
        <p className="text-sm text-secondary mb-6">
          {upgrading ? t.planSwitch.upgradeDescription : t.planSwitch.downgradeDescription}
        </p>

        {error && (
          <div className="mb-4 glass border border-red-500/20 text-red-400 px-4 py-3 rounded-xl text-sm">
            {error}
          </div>
        )}

        {upgrading ? (
          /* Upgrade UI */
          <div className="space-y-4">
            <div className="p-4 rounded-xl bg-violet-500/10 border border-violet-500/20">
              <p className="text-sm text-violet-300 font-medium mb-2">
                {targetLabel} ({targetPrice}) {t.planSwitch.upgradeIncludes}
              </p>
              <ul className="text-sm text-violet-300/80 space-y-1.5">
                <li>• {t.planSwitch.noApiKeyNeeded}</li>
                <li>• {t.planSwitch.smartRoutingIncluded}</li>
                <li>• {targetPlan === "pro_plus" ? t.planSwitch.proPlusMonthlyCredits : t.planSwitch.monthlyCredits}</li>
              </ul>
            </div>
            {subscription.isTrialing ? (
              <p className="text-sm text-muted">
                {t.planSwitch.upgradeTrialNote}
              </p>
            ) : (
              <p className="text-sm text-muted">
                {t.planSwitch.upgradeChargeNote}
              </p>
            )}
          </div>
        ) : (
          /* Downgrade UI — always scheduled at period end */
          <div className="space-y-4">
            {subscription.currentPeriodEnd && (
              <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/20">
                <p className="text-sm text-amber-300">
                  {t.planSwitch.switchesOn}{" "}
                  {new Date(subscription.currentPeriodEnd).toLocaleDateString()}
                </p>
                <p className="text-xs text-amber-300/70 mt-1">
                  {t.planSwitch.atPeriodEnd}
                </p>
              </div>
            )}

            {needsApiKey && (
              <div className="space-y-3">
                {/* Primary key — required for current model */}
                {modelIsFireworks ? (
                  <div>
                    <Input
                      label={t.planSwitch.fireworksApiKeyLabel}
                      type="password"
                      value={fireworksKey}
                      onChange={(e) => setFireworksKey(e.target.value)}
                      placeholder="fw-..."
                    />
                    <p className="text-xs text-muted mt-1.5">
                      {t.planSwitch.apiKeyHint}
                    </p>
                  </div>
                ) : (
                  <div>
                    <Input
                      label={t.planSwitch.apiKeyLabel}
                      type="password"
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder="sk-ant-..."
                    />
                    <p className="text-xs text-muted mt-1.5">
                      {t.planSwitch.apiKeyHint}
                    </p>
                  </div>
                )}
                {/* Advanced: optional secondary key */}
                <button
                  type="button"
                  onClick={() => setShowAdvanced(!showAdvanced)}
                  className="flex items-center gap-1.5 text-xs text-secondary hover:text-foreground transition-colors cursor-pointer"
                >
                  <svg
                    className={`w-3 h-3 transition-transform duration-200 ${showAdvanced ? "rotate-90" : ""}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                  Advanced
                </button>
                {showAdvanced && (
                  <div>
                    {modelIsFireworks ? (
                      <div>
                        <Input
                          label={t.planSwitch.apiKeyLabel}
                          type="password"
                          value={apiKey}
                          onChange={(e) => setApiKey(e.target.value)}
                          placeholder="sk-ant-..."
                        />
                        <p className="text-xs text-muted mt-1.5">{t.onboarding.anthropicApiKeyHint}</p>
                      </div>
                    ) : (
                      <div>
                        <Input
                          label={t.planSwitch.fireworksApiKeyLabel}
                          type="password"
                          value={fireworksKey}
                          onChange={(e) => setFireworksKey(e.target.value)}
                          placeholder="fw-..."
                        />
                        <p className="text-xs text-muted mt-1.5">{t.onboarding.fireworksApiKeyHint}</p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div className="flex gap-3 mt-6 pt-4 border-t border-black/[0.08]">
          <Button
            variant="ghost"
            size="md"
            onClick={handleClose}
            disabled={submitting}
          >
            {t.planSwitch.cancel}
          </Button>
          <Button
            variant="cta"
            size="md"
            onClick={handleConfirm}
            disabled={submitting}
          >
            {submitting ? t.planSwitch.switching : t.planSwitch.confirmSwitch}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
