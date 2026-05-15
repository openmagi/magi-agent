/**
 * Lightweight GA4 + PostHog event tracking wrapper.
 *
 * All event names follow snake_case convention per GA4 recommendations.
 * Safe to call server-side (no-ops when `window` is unavailable).
 */

import posthog from "posthog-js";

type EventParams = Record<string, string | number | boolean | null | undefined>;

export function trackEvent(name: string, params?: EventParams): void {
  if (typeof window === "undefined") return;

  // GA4
  if (window.gtag) {
    window.gtag("event", name, params);
  }

  // PostHog (respect cookie consent opt-out)
  if (posthog.__loaded && !posthog.has_opted_out_capturing()) {
    posthog.capture(name, params);
  }
}

/* ─── Onboarding funnel ─── */

export function trackOnboardingStart(): void {
  trackEvent("onboarding_start");
}

export function trackOnboardingStep(step: number, stepName: string): void {
  trackEvent("onboarding_step_view", { step_number: step, step_name: stepName });
}

export function trackOnboardingModelSelect(model: string): void {
  trackEvent("onboarding_model_select", { model });
}

export function trackOnboardingTelegramValidate(success: boolean): void {
  trackEvent("onboarding_telegram_validate", { success });
}

export function trackOnboardingPurposeSelect(preset: string | null): void {
  trackEvent("onboarding_purpose_select", { preset: preset ?? "custom" });
}

export function trackOnboardingAbandon(step: number, stepName: string): void {
  trackEvent("onboarding_abandon", { step_number: step, step_name: stepName });
}

export function trackOnboardingStepBack(fromStep: number, fromStepName: string): void {
  trackEvent("onboarding_step_back", { from_step: fromStep, from_step_name: fromStepName });
}

export function trackOnboardingPurposeSkip(): void {
  trackEvent("onboarding_purpose_skip");
}

export function trackOnboardingBotfatherClick(): void {
  trackEvent("onboarding_botfather_click");
}

export function trackOnboardingNewbotCopy(): void {
  trackEvent("onboarding_newbot_copy");
}

export function trackOnboardingTelegramStartClick(): void {
  trackEvent("onboarding_telegram_start_click");
}

export function trackOnboardingProvisioningEnter(): void {
  trackEvent("onboarding_provisioning_enter");
}

export function trackOnboardingPlanSelect(plan: string): void {
  trackEvent("onboarding_plan_select", { plan });
}

export function trackOnboardingDeploy(plan: string, model: string): void {
  trackEvent("onboarding_deploy_click", { plan, model });
  // Google Ads conversion — fires on deploy CTA click
  if (typeof window !== "undefined" && window.gtag) {
    window.gtag("event", "conversion", {
      send_to: "AW-17978685462/kez0CMuJ9IAcEJbw8_xC",
      value: 15.0,
      currency: "USD",
    });
  }
}

export function trackOnboardingDeploySuccess(): void {
  trackEvent("onboarding_deploy_success");
}

export function trackOnboardingDeployError(error: string): void {
  trackEvent("onboarding_deploy_error", { error: error.slice(0, 100) });
}

export function trackOnboardingComplete(): void {
  trackEvent("onboarding_complete");
}

/* ─── Landing page ─── */

export function trackCtaClick(location: string, variant: string): void {
  trackEvent("cta_click", { location, variant });
}

export function trackPricingPlanClick(plan: string): void {
  trackEvent("pricing_plan_click", { plan });
}

/* ─── Auth ─── */

export function trackAuthClick(action: "login" | "signup", method?: string): void {
  trackEvent("auth_click", { action, method: method ?? "page" });
}

/* ─── Blog ─── */

export function trackBlogPostView(slug: string, locale: string): void {
  trackEvent("blog_post_view", { slug, locale });
}

/* ─── Dashboard ─── */

export function trackSettingsSave(model: string): void {
  trackEvent("settings_save", { model });
}

export function trackBotRetry(): void {
  trackEvent("bot_retry_click");
}

export function trackCreditsPurchase(amountCents: number): void {
  trackEvent("credits_purchase_click", { amount_cents: amountCents });
}

export function trackSubscriptionManage(): void {
  trackEvent("subscription_manage_click");
}

/* ─── /demo funnel ─── */

export function trackDemoVisit(): void {
  trackEvent("demo_visit");
}

export function trackDemoChannelSelect(channel: string): void {
  trackEvent("demo_channel_select", { channel });
}

export function trackDemoSpawnClick(): void {
  trackEvent("demo_spawn_click");
}
