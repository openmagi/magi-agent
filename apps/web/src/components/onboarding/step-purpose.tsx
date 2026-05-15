"use client";

import { useState } from "react";
import { setOnboardingState, getOnboardingState } from "@/lib/onboarding/store";
import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";
import { trackOnboardingPurposeSelect, trackOnboardingPurposeSkip } from "@/lib/analytics";
import { PURPOSE_OPTIONS } from "@/lib/skills-catalog";

interface StepPurposeProps {
  onNext: () => void;
  onBack: () => void;
}

export function StepPurpose({ onNext, onBack }: StepPurposeProps) {
  const state = getOnboardingState();
  const t = useMessages();

  const [selectedCategory, setSelectedCategory] = useState<string | null>(state.purposeCategory ?? null);
  const onboardingMessages = t.onboarding as Record<string, string>;

  function handleNext() {
    if (selectedCategory) {
      trackOnboardingPurposeSelect(selectedCategory);
      setOnboardingState({
        purposeCategory: selectedCategory,
        purposePreset: null,
        botPurpose: null,
        step: 1,
      });
    }
    onNext();
  }

  function handleSkip() {
    trackOnboardingPurposeSkip();
    setOnboardingState({ purposeCategory: "general", step: 1 });
    onNext();
  }

  return (
    <div>
      <h1 className="text-xl font-bold mb-1 text-gradient">{t.onboarding.purposeTitle}</h1>
      <p className="text-secondary text-sm mb-5">{t.onboarding.purposeSubtitle}</p>

      {/* Purpose category grid */}
      <div className="grid grid-cols-2 gap-2">
        {PURPOSE_OPTIONS.map((opt) => (
          <button
            key={opt.id}
            onClick={() => setSelectedCategory(opt.id)}
            className="w-full text-left cursor-pointer"
          >
            <GlassCard
              hover
              className={`!p-3.5 !rounded-xl transition-all duration-200 ${
                selectedCategory === opt.id
                  ? "gradient-border glow-sm"
                  : ""
              }`}
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-lg">{opt.emoji}</span>
                <span className="font-semibold text-sm text-foreground">
                  {onboardingMessages[opt.label] ?? opt.id}
                </span>
              </div>
              <p className="text-[11px] text-secondary leading-relaxed">
                {onboardingMessages[opt.descriptionKey] ?? ""}
              </p>
            </GlassCard>
          </button>
        ))}
      </div>

      <div className="flex gap-3 mt-5">
        <Button
          variant="secondary"
          onClick={onBack}
          size="md"
          className="flex-1"
        >
          {t.onboarding.back}
        </Button>
        <Button
          onClick={handleNext}
          size="md"
          className="flex-1"
          disabled={!selectedCategory}
        >
          {t.onboarding.continue}
        </Button>
      </div>

      <button
        onClick={handleSkip}
        className="w-full mt-3 text-sm text-secondary hover:text-foreground transition-colors cursor-pointer"
      >
        {t.onboarding.skip}
      </button>
    </div>
  );
}
