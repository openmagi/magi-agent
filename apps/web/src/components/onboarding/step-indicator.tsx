"use client";

import { useMessages } from "@/lib/i18n";
import { ONBOARDING_STEPS } from "@/lib/onboarding/types";

interface StepIndicatorProps {
  currentStep: number;
}

export function StepIndicator({ currentStep }: StepIndicatorProps) {
  const t = useMessages();
  const stepLabels = [
    t.onboarding.stepPurpose,
    t.onboarding.stepPersonality,
    t.onboarding.stepDeploy,
  ];

  return (
    <div className="flex items-center justify-center">
      {ONBOARDING_STEPS.map((step, i) => (
        <div key={step.label} className="flex items-center">
          <div className="flex items-center gap-1.5">
            <div
              className={`w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-semibold transition-all duration-300 ${
                i < currentStep
                  ? "bg-gradient-to-r from-primary to-cta text-white"
                  : i === currentStep
                    ? "bg-gradient-to-r from-primary to-cta text-white glow-sm"
                    : "bg-black/10 text-secondary/50"
              }`}
            >
              {i < currentStep ? (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              ) : (
                i + 1
              )}
            </div>
            <span
              className={`text-xs hidden sm:inline transition-colors duration-300 ${
                i <= currentStep ? "text-foreground font-medium" : "text-secondary/50"
              }`}
            >
              {stepLabels[i]}
            </span>
          </div>

          {i < ONBOARDING_STEPS.length - 1 && (
            <div
              className={`w-8 h-px mx-2 transition-colors duration-300 ${
                i < currentStep
                  ? "bg-gradient-to-r from-primary to-cta"
                  : "bg-black/10"
              }`}
            />
          )}
        </div>
      ))}
    </div>
  );
}
