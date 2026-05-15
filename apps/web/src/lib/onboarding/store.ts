"use client";

import type { OnboardingState } from "./types";

const STORAGE_KEY = "clawy_onboarding";

const DEFAULT_STATE: OnboardingState = {
  step: 0,
  modelSelection: "clawy_smart_routing",
  routerType: "standard",
  language: "auto",
  apiKeyMode: null,
  anthropicApiKey: null,
  fireworksApiKey: null,
  openaiApiKey: null,
  geminiApiKey: null,
  codexAccessToken: null,
  codexRefreshToken: null,
  customBaseUrl: null,
  botPurpose: null,
  purposePreset: null,
  pricingTier: undefined,
  pendingDeploy: false,
};

export function getOnboardingState(): OnboardingState {
  if (typeof window === "undefined") return DEFAULT_STATE;
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored ? { ...DEFAULT_STATE, ...JSON.parse(stored) } : DEFAULT_STATE;
  } catch {
    return DEFAULT_STATE;
  }
}

export function setOnboardingState(partial: Partial<OnboardingState>): OnboardingState {
  const current = getOnboardingState();
  const next = { ...current, ...partial };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

export function clearOnboardingState(): void {
  localStorage.removeItem(STORAGE_KEY);
}
