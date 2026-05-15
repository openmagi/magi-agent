import { createAdminClient } from "@/lib/supabase/admin";
import { getStripe } from "@/lib/api/stripe";
import { reprovisionUserBot } from "@/lib/provisioning/trigger";
import {
  PLAN_PRICE_CENTS,
  isUpgrade,
  isDowngrade,
  getPriceId,
  getApiKeyMode,
} from "@/lib/billing/plans";
import type { SubscriptionPlan } from "@/lib/supabase/types";

export type SwitchType = "upgrade" | "downgrade_scheduled" | "trial_switch";

export interface SwitchPlanInput {
  userId: string;
  targetPlan: SubscriptionPlan;
  anthropicApiKey?: string;
  fireworksApiKey?: string;
  customBaseUrl?: string;
}

export interface SwitchPlanResult {
  success: boolean;
  newPlan: string;
  effectiveAt: string;
  prorationCreditCents: number;
  switchType: SwitchType;
}

/** Calculate remaining trial days from a shared 7-day trial */
export function calculateRemainingTrialDays(trialStartedAt: string): number {
  const start = new Date(trialStartedAt);
  const now = new Date();
  const elapsed = now.getTime() - start.getTime();
  const elapsedDays = elapsed / (1000 * 60 * 60 * 24);
  return Math.max(0, Math.ceil(7 - elapsedDays));
}

/** Calculate prorated refund for an upgrade from a lower-priced plan */
export function calculateProrationCreditCents(
  periodStart: number,
  periodEnd: number,
  planPriceCents: number,
): number {
  const totalPeriodMs = (periodEnd - periodStart) * 1000;
  const remainingMs = periodEnd * 1000 - Date.now();
  if (totalPeriodMs <= 0 || remainingMs <= 0) return 0;
  const fraction = remainingMs / totalPeriodMs;
  return Math.round(planPriceCents * fraction);
}

export async function switchPlan(input: SwitchPlanInput): Promise<SwitchPlanResult> {
  const { userId, targetPlan, anthropicApiKey, fireworksApiKey, customBaseUrl } = input;
  const stripe = getStripe();
  const supabase = createAdminClient();

  // Load subscription
  const { data: sub } = await supabase
    .from("subscriptions")
    .select("*")
    .eq("user_id", userId)
    .single();

  if (!sub) throw new Error("No subscription found");
  if (sub.status === "canceled") throw new Error("Subscription is canceled");
  if (sub.status === "past_due") throw new Error("Resolve payment issue first");
  if (sub.plan === targetPlan) throw new Error("Already on this plan");

  const newApiKeyMode = getApiKeyMode(targetPlan);
  const currentPlan = sub.plan as SubscriptionPlan;

  // --- TRIAL SWITCH (no Stripe subscription yet, e.g. admin-granted trial) ---
  if (sub.status === "trialing" && !sub.stripe_subscription_id) {
    if (isUpgrade(currentPlan, targetPlan)) {
      throw new Error("Checkout required to upgrade trial plan");
    }

    await supabase.from("subscriptions").update({
      plan: targetPlan,
      scheduled_plan: null,
      scheduled_change_at: null,
    }).eq("user_id", userId);

    await supabase.from("bots").update({ api_key_mode: newApiKeyMode })
      .eq("user_id", userId).in("status", ["active", "provisioning"]);

    if (targetPlan === "byok" && anthropicApiKey) {
      await supabase.from("profiles").update({ anthropic_api_key: anthropicApiKey }).eq("id", userId);
    }
    if (targetPlan === "byok" && fireworksApiKey) {
      await supabase.from("profiles").update({ fireworks_api_key: fireworksApiKey }).eq("id", userId);
    }
    if (targetPlan === "byok" && customBaseUrl) {
      await supabase.from("profiles").update({ custom_base_url: customBaseUrl }).eq("id", userId);
    }

    await reprovisionUserBot(userId);

    await logSwitch(supabase, userId, currentPlan, targetPlan, "trial_switch", 0, true);

    return {
      success: true,
      newPlan: targetPlan,
      effectiveAt: new Date().toISOString(),
      prorationCreditCents: 0,
      switchType: "trial_switch",
    };
  }

  // From here on, Stripe subscription is required
  if (!sub.stripe_subscription_id) {
    throw new Error("No Stripe subscription found — cannot modify plan");
  }

  const stripeSub = await stripe.subscriptions.retrieve(sub.stripe_subscription_id);
  // Cast to access period fields that exist at runtime
  const stripeSubRaw = stripeSub as unknown as Record<string, unknown>;
  const isTrialing = stripeSub.status === "trialing";
  const currentItemId = stripeSub.items.data[0].id;

  const targetPriceId = getPriceId(targetPlan);

  // --- TRIAL SWITCH (with Stripe subscription) ---
  if (isTrialing) {
    const remainingDays = sub.trial_started_at
      ? calculateRemainingTrialDays(sub.trial_started_at)
      : 7;

    const trialEnd = Math.floor(Date.now() / 1000) + remainingDays * 86400;

    await stripe.subscriptions.update(stripeSub.id, {
      items: [{ id: currentItemId, price: targetPriceId }],
      trial_end: trialEnd,
      proration_behavior: "none",
    });

    await supabase.from("subscriptions").update({
      plan: targetPlan,
      scheduled_plan: null,
      scheduled_change_at: null,
    }).eq("user_id", userId);

    await supabase.from("bots").update({ api_key_mode: newApiKeyMode })
      .eq("user_id", userId).in("status", ["active", "provisioning"]);

    if (targetPlan === "byok" && anthropicApiKey) {
      await supabase.from("profiles").update({ anthropic_api_key: anthropicApiKey }).eq("id", userId);
    }
    if (targetPlan === "byok" && fireworksApiKey) {
      await supabase.from("profiles").update({ fireworks_api_key: fireworksApiKey }).eq("id", userId);
    }
    if (targetPlan === "byok" && customBaseUrl) {
      await supabase.from("profiles").update({ custom_base_url: customBaseUrl }).eq("id", userId);
    }

    await reprovisionUserBot(userId);

    await logSwitch(supabase, userId, currentPlan, targetPlan, "trial_switch", 0, true);

    return {
      success: true,
      newPlan: targetPlan,
      effectiveAt: new Date().toISOString(),
      prorationCreditCents: 0,
      switchType: "trial_switch",
    };
  }

  // --- UPGRADE ---
  if (isUpgrade(currentPlan, targetPlan)) {
    const oldApiKeyMode = getApiKeyMode(currentPlan);

    await stripe.subscriptions.update(stripeSub.id, {
      items: [{ id: currentItemId, price: targetPriceId }],
      proration_behavior: "always_invoice",
    });

    const oldPriceCents = PLAN_PRICE_CENTS[currentPlan] ?? 0;
    const prorationCents = calculateProrationCreditCents(
      stripeSubRaw.current_period_start as number,
      stripeSubRaw.current_period_end as number,
      oldPriceCents,
    );

    if (prorationCents > 0) {
      await supabase.from("credit_transactions").insert({
        user_id: userId,
        amount_cents: prorationCents,
        type: "proration",
        description: `${currentPlan} -> ${targetPlan} upgrade proration credit ($${(prorationCents / 100).toFixed(2)})`,
      });
      await supabase.rpc("increment_credits", { p_user_id: userId, p_amount: prorationCents });
    }

    await supabase.from("subscriptions").update({
      plan: targetPlan,
      scheduled_plan: null,
      scheduled_change_at: null,
    }).eq("user_id", userId);

    await supabase.from("bots").update({ api_key_mode: newApiKeyMode })
      .eq("user_id", userId).in("status", ["active", "provisioning"]);

    // Reprovision if the api_key_mode actually changed (e.g. byok -> pro)
    if (oldApiKeyMode !== newApiKeyMode) {
      await reprovisionUserBot(userId);
    }

    await logSwitch(supabase, userId, currentPlan, targetPlan, "upgrade", prorationCents, false);

    return {
      success: true,
      newPlan: targetPlan,
      effectiveAt: new Date().toISOString(),
      prorationCreditCents: prorationCents,
      switchType: "upgrade",
    };
  }

  // --- DOWNGRADE (always scheduled at period end) ---
  if (isDowngrade(currentPlan, targetPlan)) {
    if (targetPlan === "byok" && !anthropicApiKey && !fireworksApiKey) {
      // Verify the user already has at least one API key stored
      const { data: profile } = await supabase
        .from("profiles")
        .select("anthropic_api_key, fireworks_api_key")
        .eq("id", userId)
        .single();

      if (!profile?.anthropic_api_key && !profile?.fireworks_api_key) {
        throw new Error("API key required for BYOK plan");
      }
    }

    if (targetPlan === "byok" && anthropicApiKey) {
      await supabase.from("profiles").update({ anthropic_api_key: anthropicApiKey }).eq("id", userId);
    }
    if (targetPlan === "byok" && fireworksApiKey) {
      await supabase.from("profiles").update({ fireworks_api_key: fireworksApiKey }).eq("id", userId);
    }
    if (targetPlan === "byok" && customBaseUrl) {
      await supabase.from("profiles").update({ custom_base_url: customBaseUrl }).eq("id", userId);
    }

    const periodEnd = new Date((stripeSubRaw.current_period_end as number) * 1000).toISOString();

    await supabase.from("subscriptions").update({
      scheduled_plan: targetPlan,
      scheduled_change_at: periodEnd,
    }).eq("user_id", userId);

    await logSwitch(supabase, userId, currentPlan, targetPlan, "downgrade_scheduled", 0, false);

    return {
      success: true,
      newPlan: currentPlan, // Still on current plan until period end
      effectiveAt: periodEnd,
      prorationCreditCents: 0,
      switchType: "downgrade_scheduled",
    };
  }

  throw new Error("Invalid plan switch combination");
}

export async function cancelScheduledSwitch(userId: string): Promise<void> {
  const supabase = createAdminClient();

  await supabase.from("subscriptions").update({
    scheduled_plan: null,
    scheduled_change_at: null,
  }).eq("user_id", userId);
}

export async function executeScheduledSwitch(
  userId: string,
  stripeSubscriptionId: string,
): Promise<void> {
  const stripe = getStripe();
  const supabase = createAdminClient();

  const { data: sub } = await supabase
    .from("subscriptions")
    .select("*")
    .eq("user_id", userId)
    .single();

  if (!sub?.scheduled_plan) return;

  const targetPlan = sub.scheduled_plan as "byok" | "pro" | "pro_plus";
  const targetPriceId = getPriceId(targetPlan);
  const newApiKeyMode = getApiKeyMode(targetPlan);

  const stripeSub = await stripe.subscriptions.retrieve(stripeSubscriptionId);
  const currentItemId = stripeSub.items.data[0].id;

  await stripe.subscriptions.update(stripeSub.id, {
    items: [{ id: currentItemId, price: targetPriceId }],
    proration_behavior: "none",
  });

  await supabase.from("subscriptions").update({
    plan: targetPlan,
    scheduled_plan: null,
    scheduled_change_at: null,
  }).eq("user_id", userId);

  await supabase.from("bots").update({ api_key_mode: newApiKeyMode })
    .eq("user_id", userId).in("status", ["active", "provisioning"]);

  await reprovisionUserBot(userId);
}

async function logSwitch(
  supabase: ReturnType<typeof createAdminClient>,
  userId: string,
  fromPlan: string,
  toPlan: string,
  switchType: SwitchType,
  prorationCreditCents: number,
  wasTrialing: boolean,
): Promise<void> {
  await supabase.from("plan_switch_log").insert({
    user_id: userId,
    from_plan: fromPlan,
    to_plan: toPlan,
    switch_type: switchType,
    proration_credit_cents: prorationCreditCents,
    was_trialing: wasTrialing,
  });
}
