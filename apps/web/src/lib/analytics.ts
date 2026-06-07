type EventParams = Record<string, string | number | boolean | null | undefined>;

export function trackEvent(_name: string, _params?: EventParams): void {
  // Analytics providers are cloud-only. The OSS dashboard keeps this as a no-op.
}

export function trackBotRetry(): void {
  trackEvent("bot_retry_click");
}

export function trackOnboardingComplete(): void {
  trackEvent("onboarding_complete");
}

export function trackOnboardingStart(): void {
  trackEvent("onboarding_start");
}

export function trackOnboardingStep(step: number, stepName: string): void {
  trackEvent("onboarding_step_view", { step_number: step, step_name: stepName });
}

export function trackOnboardingDeploy(plan: string, model: string): void {
  trackEvent("onboarding_deploy_click", { plan, model });
}
