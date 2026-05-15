import { createAdminClient } from "@/lib/supabase/admin";
import { getStripe } from "@/lib/api/stripe";
import { safeDecrypt } from "@/lib/crypto";
import { getPriceId } from "@/lib/billing/plans";
import { prepareReferralCheckoutDiscounts } from "@/lib/referral/checkout";

/**
 * Get the user's credit balance in cents.
 */
export async function getCreditBalance(userId: string): Promise<number> {
  const supabase = createAdminClient();
  const { data: credits } = await supabase
    .from("credits")
    .select("balance_cents")
    .eq("user_id", userId)
    .single();

  return credits?.balance_cents ?? 0;
}

/**
 * Create a Stripe Checkout session for purchasing credits.
 * Returns the checkout URL.
 */
export async function createCreditCheckoutSession(
  userId: string,
  amountCents: number,
  origin: string
): Promise<string> {
  const stripe = getStripe();
  const supabase = createAdminClient();

  const { data: existingSub } = await supabase
    .from("subscriptions")
    .select("stripe_customer_id")
    .eq("user_id", userId)
    .single();

  let customerId = existingSub?.stripe_customer_id;

  if (!customerId) {
    const customer = await stripe.customers.create({
      metadata: { user_id: userId },
    });
    customerId = customer.id;
  }

  const session = await stripe.checkout.sessions.create({
    customer: customerId,
    mode: "payment",
    line_items: [
      {
        price_data: {
          currency: "usd",
          product_data: { name: "Open Magi Credits" },
          unit_amount: amountCents,
        },
        quantity: 1,
      },
    ],
    success_url: `${origin}/dashboard/billing?success=true`,
    cancel_url: `${origin}/dashboard/billing`,
    metadata: { user_id: userId },
  });

  return session.url!;
}

/**
 * Create a Stripe Billing Portal session.
 * Returns the portal URL.
 */
export async function createBillingPortalSession(
  userId: string,
  returnUrl: string
): Promise<string> {
  const stripe = getStripe();
  const supabase = createAdminClient();

  const { data: subscription } = await supabase
    .from("subscriptions")
    .select("stripe_customer_id")
    .eq("user_id", userId)
    .single();

  if (!subscription?.stripe_customer_id) {
    throw new Error("No active subscription found");
  }

  const portalSession = await stripe.billingPortal.sessions.create({
    customer: subscription.stripe_customer_id,
    return_url: returnUrl,
  });

  return portalSession.url;
}

/**
 * Create a Stripe Checkout session for a new/reactivation subscription.
 * Returns the checkout URL.
 */
export async function createSubscriptionCheckout(
  userId: string,
  origin: string
): Promise<{ checkoutUrl?: string; redirect?: string }> {
  const stripe = getStripe();
  const supabase = createAdminClient();

  // Check user has a non-active bot that needs reactivation
  const { data: bot } = await supabase
    .from("bots")
    .select(
      "id, name, status, model_selection, api_key_mode, telegram_bot_token, telegram_bot_username, telegram_user_handle, bot_purpose, purpose_preset"
    )
    .eq("user_id", userId)
    .in("status", ["stopped", "error", "provisioning"])
    .single();

  if (!bot) {
    throw new Error("No reactivatable bot found");
  }

  // Check if user already has an active subscription
  const { data: existingSub } = await supabase
    .from("subscriptions")
    .select("id, status, trial_ends_at, stripe_subscription_id")
    .eq("user_id", userId)
    .in("status", ["active", "trialing"])
    .single();

  if (existingSub) {
    // If trialing without a real Stripe subscription and trial has expired, don't skip checkout
    const isExpiredTrial =
      existingSub.status === "trialing" &&
      !existingSub.stripe_subscription_id &&
      existingSub.trial_ends_at &&
      new Date(existingSub.trial_ends_at) <= new Date();

    if (!isExpiredTrial) {
      // Already subscribed — just reactivate the bot
      await supabase
        .from("bots")
        .update({ status: "provisioning", error_message: null })
        .eq("id", bot.id);
      return { redirect: "/dashboard" };
    }
  }

  // Find or create Stripe customer
  const { data: canceledSub } = await supabase
    .from("subscriptions")
    .select("stripe_customer_id")
    .eq("user_id", userId)
    .single();

  let customerId = canceledSub?.stripe_customer_id;

  if (!customerId) {
    const customer = await stripe.customers.create({
      metadata: { user_id: userId },
    });
    customerId = customer.id;
  }

  // Read existing subscription to preserve plan tier and billing interval
  const { data: existingSub2 } = await supabase
    .from("subscriptions")
    .select("plan, billing_interval")
    .eq("user_id", userId)
    .single();
  const existingSub2Record = existingSub2 as unknown as Record<string, unknown> | null;
  const plan = bot.api_key_mode === "byok" ? "byok" : ((existingSub2Record?.plan as string) ?? "pro");
  const billingInterval = ((existingSub2Record?.billing_interval as string) ?? "monthly") as "monthly" | "yearly";
  const priceId = getPriceId(plan, billingInterval);

  const discounts = await prepareReferralCheckoutDiscounts(stripe, userId);

  const session = await stripe.checkout.sessions.create({
    customer: customerId,
    mode: "subscription",
    line_items: [{ price: priceId, quantity: 1 }],
    ...(discounts.length > 0 ? { discounts } : {}),
    success_url: `${origin}/dashboard?subscribed=true`,
    cancel_url: `${origin}/dashboard`,
    metadata: {
      user_id: userId,
      bot_id: bot.id,
      plan,
      billing_interval: billingInterval,
      reactivation: "true",
      telegram_bot_token: bot.telegram_bot_token ? safeDecrypt(bot.telegram_bot_token) : "",
      telegram_bot_username: bot.telegram_bot_username ?? "",
      telegram_user_handle: bot.telegram_user_handle ?? "",
      model_selection: bot.model_selection,
      api_key_mode: bot.api_key_mode,
      bot_purpose: bot.bot_purpose ?? "",
      purpose_preset: bot.purpose_preset ?? "",
    },
  });

  return { checkoutUrl: session.url! };
}
