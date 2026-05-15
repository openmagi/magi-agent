"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Modal } from "@/components/ui/modal";
import { StepIndicator } from "./step-indicator";
import { StepPurpose } from "./step-purpose";
import { StepPersonality } from "./step-personality";
import { StepReviewDeploy } from "./step-review-deploy";
import { getOnboardingState, clearOnboardingState } from "@/lib/onboarding/store";
import { useMessages } from "@/lib/i18n";
import { trackOnboardingStart, trackOnboardingStep, trackOnboardingAbandon, trackOnboardingStepBack } from "@/lib/analytics";

/** Deduplicate onboarding_start so OAuth re-opens don't double-fire. */
const ONBOARDING_START_KEY = "clawy_onboarding_started";

interface OnboardingModalProps {
  open: boolean;
  onClose: () => void;
  sessionId?: string | null;
  onDeployComplete: (newBotId?: string) => void;
  mode?: "create" | "add";
  subscriptionPlan?: string | null;
}

const STEP_NAMES = ["purpose", "personality", "deploy"];
const MAX_STEP = 2;

function clampStep(step: number): number {
  return Math.max(0, Math.min(step, MAX_STEP));
}

export function OnboardingModal({
  open,
  onClose,
  sessionId,
  onDeployComplete,
  mode = "create",
  subscriptionPlan,
}: OnboardingModalProps) {
  const t = useMessages();
  const deployingRef = useRef(false);

  const [currentStep, setCurrentStep] = useState(() => {
    if (sessionId) return 2; // Jump to deploy step after Stripe return
    if (mode === "add") return 0; // Fresh start for new bot
    return clampStep(getOnboardingState().step);
  });

  // Clear cached onboarding state when opening in add mode (new bot = fresh context)
  useEffect(() => {
    if (open && mode === "add") {
      clearOnboardingState();
      setCurrentStep(0);
    }
  }, [open, mode]);

  // Track onboarding start when modal opens (skip if resuming after OAuth redirect)
  useEffect(() => {
    if (open) {
      const alreadyStarted = sessionStorage.getItem(ONBOARDING_START_KEY);
      if (!alreadyStarted) {
        trackOnboardingStart();
        sessionStorage.setItem(ONBOARDING_START_KEY, "1");
      }
      trackOnboardingStep(currentStep, STEP_NAMES[currentStep]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const goNext = useCallback(() => {
    setCurrentStep((prev) => {
      const next = Math.min(prev + 1, MAX_STEP);
      trackOnboardingStep(next, STEP_NAMES[next]);
      return next;
    });
  }, []);

  const goBack = useCallback(() => {
    setCurrentStep((prev) => {
      trackOnboardingStepBack(prev, STEP_NAMES[prev]);
      return Math.max(prev - 1, 0);
    });
  }, []);

  const handleAbandon = useCallback(() => {
    // Block closing while deploy is in progress
    if (deployingRef.current) return;
    trackOnboardingAbandon(currentStep, STEP_NAMES[currentStep]);
    sessionStorage.removeItem(ONBOARDING_START_KEY);
    onClose();
  }, [currentStep, onClose]);

  const handleDeployComplete = useCallback((newBotId?: string) => {
    sessionStorage.removeItem(ONBOARDING_START_KEY);
    onClose();
    onDeployComplete(newBotId);
  }, [onClose, onDeployComplete]);

  return (
    <Modal open={open} onClose={handleAbandon}>
      <div className="p-3 sm:p-4">
        {/* Header with close button */}
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-secondary uppercase tracking-wider">
            {t.onboarding.setupWizard}
          </span>
          <button
            onClick={handleAbandon}
            className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-black/10 text-secondary hover:text-foreground transition-colors cursor-pointer"
            aria-label="Close"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Step indicator */}
        <div className="mb-3">
          <StepIndicator currentStep={currentStep} />
        </div>

        {/* Step content */}
        {currentStep === 0 && <StepPurpose onNext={goNext} onBack={handleAbandon} />}
        {currentStep === 1 && <StepPersonality onNext={goNext} onBack={goBack} />}
        {currentStep === 2 && (
          <StepReviewDeploy
            onNext={handleDeployComplete}
            onBack={goBack}
            sessionId={sessionId}
            onDeployingChange={(v) => { deployingRef.current = v; }}
            mode={mode}
            subscriptionPlan={subscriptionPlan}
          />
        )}
      </div>
    </Modal>
  );
}
