import type Stripe from "stripe";
import { createAdminClient } from "@/lib/supabase/admin";
import { encrypt } from "@/lib/crypto";
import { executeScheduledSwitch } from "@/lib/billing/plan-switch";
import { PLAN_MONTHLY_CREDITS_CENTS, PLAN_SEARCH_QUOTA } from "@/lib/billing/plans";
import { accrueReferralEarning } from "@/lib/referral/earnings";
import { recordReferral } from "@/lib/referral/checkout";
import { captureServerEvent } from "@/lib/posthog/server";

// Cast to any to avoid strict type constraints from incomplete generated Database types
// (missing Relationships/Views fields required by @supabase/postgrest-js)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function getSupabase(): any {
  return createAdminClient();
}

/** Handle checkout.session.completed — subscription or one-time payment. */
export async function handleCheckoutCompleted(
  session: Stripe.Checkout.Session
): Promise<void> {
  const supabase = getSupabase();
  const stripe = (await import("@/lib/api/stripe")).getStripe();

  if (session.mode === "subscription") {
    await handleSubscriptionCheckout(supabase, stripe, session);
  }

  if (session.mode === "payment") {
    await handlePaymentCheckout(supabase, session);
  }
}

async function handleSubscriptionCheckout(
  supabase: ReturnType<typeof getSupabase>,
  stripe: Stripe,
  session: Stripe.Checkout.Session
): Promise<void> {
  const meta = session.metadata!;
  const plan =
    meta.plan ||
    (meta.api_key_mode === "platform_credits" ? "pro" : "byok");

  const stripeSub = await stripe.subscriptions.retrieve(
    session.subscription as string
  );
  const isTrialing = stripeSub.status === "trialing";

  const subRecord = stripeSub as unknown as Record<string, unknown>;
  const billingInterval = meta.billing_interval === "yearly" ? "yearly" : "monthly";
  await supabase.from("subscriptions").upsert(
    {
      user_id: meta.user_id,
      stripe_customer_id: session.customer as string,
      stripe_subscription_id: session.subscription as string,
      plan,
      billing_interval: billingInterval,
      status: isTrialing ? "trialing" : "active",
      trial_started_at: isTrialing ? new Date().toISOString() : null,
      trial_ends_at: isTrialing
        ? new Date(stripeSub.trial_end! * 1000).toISOString()
        : null,
      current_period_end:
        typeof subRecord.current_period_end === "number"
          ? new Date((subRecord.current_period_end as number) * 1000).toISOString()
          : null,
    },
    { onConflict: "user_id" }
  );

  // Reactivation flow
  if (meta.reactivation === "true" && meta.bot_id) {
    await supabase
      .from("bots")
      .update({ status: "provisioning", error_message: null })
      .eq("id", meta.bot_id)
      .eq("user_id", meta.user_id);
    return;
  }

  // Guard against duplicate bot creation
  const { data: existingBots } = await supabase
    .from("bots")
    .select("id")
    .eq("user_id", meta.user_id)
    .in("status", ["active", "provisioning"]);

  if (existingBots && existingBots.length > 0) return;

  const hasTelegram = !!meta.telegram_bot_token;

  const ADJECTIVES = ["swift", "bright", "calm", "bold", "keen", "warm", "cool", "wild", "fair", "wise"];
  const NOUNS = ["fox", "owl", "bear", "hawk", "lynx", "wolf", "hare", "wren", "pike", "dove"];
  const randomName = `${ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)]}_${NOUNS[Math.floor(Math.random() * NOUNS.length)]}_bot`;

  const { data: newBot } = await supabase.from("bots").insert({
    user_id: meta.user_id,
    name: meta.telegram_bot_username || randomName,
    telegram_bot_token: hasTelegram
      ? encrypt(meta.telegram_bot_token!)
      : null,
    telegram_bot_username: meta.telegram_bot_username || null,
    telegram_user_handle: meta.telegram_user_handle || null,
    model_selection: meta.model_selection as
      | "clawy_smart_routing"
      | "gpt_smart_routing"
      | "smart_routing"
      | "haiku"
      | "sonnet"
      | "opus",
    api_key_mode: meta.api_key_mode as "byok" | "platform_credits",
    router_type: meta.router_type || "standard",
    bot_purpose: meta.bot_purpose || null,
    purpose_preset: meta.purpose_preset || null,
    language: meta.language || "auto",
    disabled_skills: meta.disabled_skills ? JSON.parse(meta.disabled_skills) : [],
    purpose_category: meta.purpose_category || null,
    status: "provisioning",
  }).select("id").single();

  // Always trigger provisioning — Telegram is optional
  if (newBot?.id) {
    const { triggerProvisioning } = await import("@/lib/provisioning/trigger");
    await triggerProvisioning(newBot.id);
  }

  await supabase.from("credits").upsert(
    { user_id: meta.user_id, balance_cents: 0 },
    { onConflict: "user_id", ignoreDuplicates: true }
  );

  // Seed credit_grants for new Pro/Pro+ users (non-accumulating grant tracking)
  const grantCents = PLAN_MONTHLY_CREDITS_CENTS[plan];
  if (grantCents) {
    await supabase.from("credit_grants").upsert(
      { user_id: meta.user_id, granted_cents: grantCents, used_cents: 0 },
      { onConflict: "user_id" }
    );
  }

  await supabase
    .from("profiles")
    .update({ onboarding_completed: true })
    .eq("id", meta.user_id);

  captureServerEvent(meta.user_id, "subscription_started", { plan });

  // Record referral (safety net — primary binding happens at bot creation via bindReferral)
  const discount = (session as unknown as Record<string, unknown>)
    .discount as Record<string, unknown> | undefined;
  const couponMeta = (discount?.coupon as Record<string, unknown>)
    ?.metadata as Record<string, string> | undefined;
  if (couponMeta?.referral_code_id) {
    try {
      // Skip if referral already exists (referee_id has UNIQUE constraint)
      const { data: existingRef } = await supabase
        .from("referrals")
        .select("id")
        .eq("referee_id", meta.user_id)
        .single();

      if (!existingRef) {
        await recordReferral(
          couponMeta.referrer_id,
          meta.user_id,
          couponMeta.referral_code_id,
          (discount?.coupon as Record<string, unknown>)?.id as string
        );
      }
    } catch {
      console.error(
        "[webhook] Failed to record referral for",
        meta.user_id
      );
    }
  }
}

async function handlePaymentCheckout(
  supabase: ReturnType<typeof getSupabase>,
  session: Stripe.Checkout.Session
): Promise<void> {
  const meta = session.metadata!;
  const amountCents = session.amount_total!;
  const paymentId = session.payment_intent as string;
  const userId = meta.user_id ?? meta.userId;

  if (meta.type === "org_credit_topup") {
    const orgId = meta.org_id ?? meta.orgId;
    if (!userId || !orgId) {
      throw new Error("Missing organization top-up metadata");
    }

    const { data: claimed, error: claimErr } = await supabase.rpc(
      "claim_org_stripe_credit" as never,
      {
        p_org_id: orgId,
        p_user_id: userId,
        p_stripe_payment_id: paymentId,
        p_amount_cents: amountCents,
        p_description: "Organization credit top-up",
      } as never,
    );
    if (claimErr) {
      console.error("[webhook] org top-up claim error:", claimErr.message);
      throw claimErr;
    }
    if (claimed !== true) return;

    captureServerEvent(userId, "org_credit_topup", {
      org_id: orgId,
      amount_cents: amountCents,
    });
    return;
  }

  if (!userId) {
    throw new Error("Missing payment checkout user metadata");
  }

  // Atomic: insert transaction + increment balance in one SQL boundary.
  // Route-level event_id dedupe (stripe_webhook_events) already handled;
  // pass null event_id here so we dedupe only on stripe_payment_id.
  const { data: claimed, error: claimErr } = await supabase.rpc(
    "claim_stripe_credit" as never,
    {
      p_event_id: null,
      p_event_type: "checkout.session.completed",
      p_user_id: userId,
      p_stripe_payment_id: paymentId,
      p_amount_cents: amountCents,
      p_type: "purchase",
      p_description: "Credit purchase",
    } as never,
  );
  if (claimErr) {
    console.error("[webhook] handlePaymentCheckout claim error:", claimErr.message);
    throw claimErr;
  }
  if (claimed !== true) return; // already credited

  captureServerEvent(userId, "payment_received", {
    amount_cents: amountCents,
    type: "credit_purchase",
  });

  await accrueReferralEarning(userId, amountCents, "credit_purchase", paymentId);
}

/** Handle invoice.paid — grant monthly Pro credits. */
export async function handleInvoicePaid(
  paidInvoice: Stripe.Invoice
): Promise<void> {
  const supabase = getSupabase();
  const paidCustomerId = paidInvoice.customer as string;

  // Only process subscription invoices
  const invoiceSubId = (
    paidInvoice as unknown as Record<string, unknown>
  ).subscription as string | undefined;
  if (!invoiceSubId) return;

  // Skip $0 invoices (trial period)
  if ((paidInvoice.amount_paid ?? 0) === 0) return;

  let { data: sub } = await supabase
    .from("subscriptions")
    .select("user_id, plan, scheduled_plan, stripe_subscription_id")
    .eq("stripe_customer_id", paidCustomerId)
    .single();

  // Race condition fallback: auto-conversion may not have updated DB yet.
  // Resolve user_id from Stripe subscription metadata and retry.
  if (!sub) {
    console.warn(
      `[webhook] invoice.paid: no subscription found for customer ${paidCustomerId}, trying metadata fallback`
    );
    try {
      const stripe = (await import("@/lib/api/stripe")).getStripe();
      const stripeSub = await stripe.subscriptions.retrieve(
        invoiceSubId as string
      );
      const metaUserId = stripeSub.metadata?.user_id;
      if (metaUserId) {
        // Brief wait for worker to finish DB update
        await new Promise((r) => setTimeout(r, 3000));
        const { data: retrySub } = await supabase
          .from("subscriptions")
          .select("user_id, plan, scheduled_plan, stripe_subscription_id")
          .eq("user_id", metaUserId)
          .single();
        sub = retrySub;
        if (sub) {
          console.info(
            `[webhook] invoice.paid: metadata fallback succeeded for user ${metaUserId}`
          );
        }
      }
    } catch (err) {
      console.error("[webhook] invoice.paid: metadata fallback error", err);
    }
  }

  if (!sub) {
    console.error(
      `[webhook] invoice.paid: subscription not found after fallback, customer=${paidCustomerId}, invoice=${paidInvoice.id}`
    );
    return;
  }

  // Execute scheduled plan switch if pending
  if (sub.scheduled_plan && sub.stripe_subscription_id) {
    try {
      await executeScheduledSwitch(
        sub.user_id,
        sub.stripe_subscription_id
      );
    } catch (err) {
      console.error(
        `[webhook] Scheduled switch failed for user ${sub.user_id}:`,
        err
      );
    }
    const { data: updatedSub } = await supabase
      .from("subscriptions")
      .select("plan")
      .eq("stripe_customer_id", paidCustomerId)
      .single();
    if (!["pro", "pro_plus"].includes(updatedSub?.plan ?? "")) return;
  }

  // Resolve plan — fall back to Stripe price nickname if DB plan not credit-eligible
  // (handles race condition where worker hasn't updated plan yet)
  let currentPlan = sub.plan as string;
  if (!PLAN_MONTHLY_CREDITS_CENTS[currentPlan]) {
    try {
      const stripe = (await import("@/lib/api/stripe")).getStripe();
      const stripeSub = await stripe.subscriptions.retrieve(
        invoiceSubId as string
      );
      const priceNickname = stripeSub.items?.data?.[0]?.price?.nickname;
      if (priceNickname) {
        const resolved = priceNickname.toLowerCase().replace(/\s+/g, "_");
        if (PLAN_MONTHLY_CREDITS_CENTS[resolved]) {
          console.info(
            `[webhook] invoice.paid: resolved plan from Stripe price nickname: ${resolved} (DB had: ${currentPlan})`
          );
          currentPlan = resolved;
        }
      }
    } catch {
      // Non-fatal — proceed with DB plan
    }
  }

  const searchLimit = PLAN_SEARCH_QUOTA[currentPlan] ?? 0;

  await supabase.rpc("reset_search_quota", {
    p_user_id: sub.user_id,
    p_monthly_limit: searchLimit,
  });

  // Grant monthly platform credits for Pro/Pro+ users — non-accumulating.
  // Use claim_stripe_credit with the invoice id as the dedupe key so a
  // crash between transaction-insert and balance-increment cannot leave a
  // paid-but-uncredited user on webhook retry.
  const monthlyCredits = PLAN_MONTHLY_CREDITS_CENTS[currentPlan];
  if (monthlyCredits) {
    const { data: grantAmount } = await supabase.rpc("reset_credit_grant", {
      p_user_id: sub.user_id,
      p_granted_cents: monthlyCredits,
    });
    const amount = (grantAmount as number | null) ?? monthlyCredits;
    if (amount > 0) {
      const { error: claimErr } = await supabase.rpc(
        "claim_stripe_credit" as never,
        {
          p_event_id: null,
          p_event_type: "invoice.paid",
          p_user_id: sub.user_id,
          p_stripe_payment_id: paidInvoice.id,
          p_amount_cents: amount,
          p_type: "bonus",
          p_description: `Monthly plan credits ($${(amount / 100).toFixed(2)})`,
        } as never,
      );
      if (claimErr) {
        console.error("[webhook] handleInvoicePaid monthly grant claim error:", claimErr.message);
        throw claimErr;
      }
    }
  }

  // Accrue referral earnings
  const invoiceAmountCents = paidInvoice.amount_paid ?? 0;
  if (invoiceAmountCents > 0) {
    await accrueReferralEarning(
      sub.user_id,
      invoiceAmountCents,
      "subscription",
      paidInvoice.id ?? null,
    );
  }
}

/** Handle invoice.payment_failed — attempt credit-based auto-renewal, else mark past_due. */
export async function handleInvoiceFailed(
  invoice: Stripe.Invoice
): Promise<void> {
  const supabase = getSupabase();
  const customerId = invoice.customer as string;
  const amountDue = invoice.amount_remaining ?? invoice.amount_due ?? 0;

  if (amountDue <= 0) return;

  // Only attempt credit-based renewal for subscription invoices
  const invoiceSub = (invoice as unknown as Record<string, unknown>).subscription;
  if (!invoiceSub) {
    await supabase
      .from("subscriptions")
      .update({ status: "past_due" })
      .eq("stripe_customer_id", customerId);
    return;
  }

  const { data: sub } = await supabase
    .from("subscriptions")
    .select("user_id, plan")
    .eq("stripe_customer_id", customerId)
    .single();

  if (!sub) {
    await supabase
      .from("subscriptions")
      .update({ status: "past_due" })
      .eq("stripe_customer_id", customerId);
    return;
  }

  // Idempotency: skip if already processed this invoice
  const { data: existingRenewal } = await supabase
    .from("credit_transactions")
    .select("id")
    .eq("user_id", sub.user_id)
    .eq("type", "subscription_renewal")
    .eq("stripe_payment_id", invoice.id)
    .limit(1);

  if (existingRenewal && existingRenewal.length > 0) return;

  // Calculate purchased credits (total balance minus remaining monthly grant)
  const purchasedCredits = await getPurchasedCreditBalance(supabase, sub.user_id);

  if (purchasedCredits >= amountDue) {
    let reservedCredits = false;
    try {
      const stripe = (await import("@/lib/api/stripe")).getStripe();

      const { data: reserved, error: reserveErr } = await supabase.rpc(
        "check_and_deduct_credits",
        {
          p_user_id: sub.user_id,
          p_amount: amountDue,
        },
      );
      if (reserveErr) {
        console.error(
          `[webhook] Credit reservation failed for user ${sub.user_id}:`,
          reserveErr
        );
        throw reserveErr;
      }
      if (reserved !== true) {
        console.warn(
          `[webhook] Credit reservation declined for user ${sub.user_id}; avoiding overdraw`
        );
        throw new Error("Insufficient credits at reservation time");
      }
      reservedCredits = true;

      // Add credit to Stripe customer balance (negative = credit for customer)
      await stripe.customers.createBalanceTransaction(customerId, {
        amount: -amountDue,
        currency: "usd",
        description: "Auto-renewal via purchased credits",
      });

      // Pay the invoice using customer credit balance
      await stripe.invoices.pay(invoice.id!);

      // Record transaction
      await supabase.from("credit_transactions").insert({
        user_id: sub.user_id,
        amount_cents: -amountDue,
        type: "subscription_renewal",
        description: `Auto-renewal via credits ($${(amountDue / 100).toFixed(2)})`,
        stripe_payment_id: invoice.id,
      });

      // Ensure subscription stays active
      await supabase
        .from("subscriptions")
        .update({ status: "active" })
        .eq("stripe_customer_id", customerId);

      captureServerEvent(sub.user_id, "subscription_credit_renewal", {
        plan: sub.plan,
        amount_cents: amountDue,
      });

      return;
    } catch (err) {
      if (reservedCredits) {
        await supabase.rpc("increment_credits", {
          p_user_id: sub.user_id,
          p_amount: amountDue,
        });
      }
      console.error(
        `[webhook] Credit-based renewal failed for user ${sub.user_id}:`,
        err
      );
      // Fall through to past_due
    }
  }

  await supabase
    .from("subscriptions")
    .update({ status: "past_due" })
    .eq("stripe_customer_id", customerId);
}

/**
 * Calculate the user's purchased credit balance (excluding remaining monthly grant).
 * Only purchased/topped-up credits should be used for subscription auto-renewal.
 */
async function getPurchasedCreditBalance(
  supabase: ReturnType<typeof getSupabase>,
  userId: string
): Promise<number> {
  const { data: credits } = await supabase
    .from("credits")
    .select("balance_cents")
    .eq("user_id", userId)
    .single();

  const totalBalance = credits?.balance_cents ?? 0;
  if (totalBalance <= 0) return 0;

  // Subtract remaining monthly grant (these are not purchased credits)
  const { data: grant } = await supabase
    .from("credit_grants")
    .select("granted_cents, used_cents")
    .eq("user_id", userId)
    .single();

  const remainingGrant = grant
    ? Math.max(0, grant.granted_cents - grant.used_cents)
    : 0;

  return Math.max(0, totalBalance - remainingGrant);
}

/** Handle customer.subscription.updated — sync subscription status. */
export async function handleSubscriptionUpdated(
  updatedSubscription: Stripe.Subscription
): Promise<void> {
  const supabase = getSupabase();
  const subRecord = updatedSubscription as unknown as Record<string, unknown>;

  // Detect plan change by comparing DB plan vs new Stripe plan
  const { data: existingSub } = await supabase
    .from("subscriptions")
    .select("plan, user_id")
    .eq("stripe_subscription_id", updatedSubscription.id)
    .single();

  const previousPlan = existingSub?.plan as string | null;
  const userId = existingSub?.user_id as string | null;

  // Extract new plan from Stripe price metadata or product
  const newPriceId = updatedSubscription.items?.data?.[0]?.price?.id;
  let newPlan: string | null = null;
  if (newPriceId) {
    // The plan will be updated below; detect from price metadata
    const metadata = updatedSubscription.items?.data?.[0]?.price?.metadata;
    if (metadata?.plan) {
      newPlan = metadata.plan as string;
    }
  }

  const updateData: Record<string, unknown> = {
    status:
      updatedSubscription.status === "trialing"
        ? "trialing"
        : updatedSubscription.status,
  };

  // Update plan if available from price metadata
  if (newPlan) {
    updateData.plan = newPlan;
  }

  if (typeof subRecord.current_period_start === "number") {
    updateData.current_period_start = new Date(
      (subRecord.current_period_start as number) * 1000
    ).toISOString();
  }
  if (typeof subRecord.current_period_end === "number") {
    updateData.current_period_end = new Date(
      (subRecord.current_period_end as number) * 1000
    ).toISOString();
  }
  if (updatedSubscription.trial_end) {
    updateData.trial_ends_at = new Date(
      updatedSubscription.trial_end * 1000
    ).toISOString();
  }

  await supabase
    .from("subscriptions")
    .update(updateData)
    .eq("stripe_subscription_id", updatedSubscription.id);

  // If plan changed between tiers that affect bot resources (e.g. vector
  // search on/off), recreate all user's active bots so env vars refresh.
  const VECTOR_PLANS = new Set(["max", "flex"]);
  const planActuallyChanged = Boolean(newPlan && previousPlan && newPlan !== previousPlan);
  const vectorTierChanged = planActuallyChanged &&
    (VECTOR_PLANS.has(newPlan ?? "") !== VECTOR_PLANS.has(previousPlan ?? ""));

  if (vectorTierChanged && userId) {
    console.log(
      `[webhook] Plan changed ${previousPlan} → ${newPlan} for user ${userId}, triggering bot recreates`
    );
    try {
      const { data: bots } = await supabase
        .from("bots")
        .select("id")
        .eq("user_id", userId)
        .eq("status", "active");

      const PW_URL = process.env.PW_INTERNAL_URL ??
        "http://provisioning-worker.clawy-system.svc.cluster.local:8080";
      const PW_API_KEY = process.env.PW_ADMIN_API_KEY ?? process.env.ADMIN_API_KEY ?? "";

      for (const bot of bots ?? []) {
        try {
          await fetch(`${PW_URL}/api/bots/${bot.id}/recreate`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "x-api-key": PW_API_KEY,
            },
            body: JSON.stringify({ userId }),
          });
          console.log(`[webhook] Bot ${bot.id} recreate triggered`);
        } catch (err) {
          console.warn(`[webhook] Bot ${bot.id} recreate failed: ${(err as Error).message}`);
        }
      }
    } catch (err) {
      console.warn(`[webhook] Failed to trigger bot recreates: ${(err as Error).message}`);
    }
  }
}

/** Handle customer.subscription.deleted — cancel subscription and stop bots. */
export async function handleSubscriptionDeleted(
  subscription: Stripe.Subscription
): Promise<void> {
  const supabase = getSupabase();

  await supabase
    .from("subscriptions")
    .update({ status: "canceled" })
    .eq("stripe_subscription_id", subscription.id);

  const { data: sub } = await supabase
    .from("subscriptions")
    .select("user_id")
    .eq("stripe_subscription_id", subscription.id)
    .single();

  if (sub) {
    await supabase
      .from("bots")
      .update({ status: "stopped" })
      .eq("user_id", sub.user_id);
  }
}
