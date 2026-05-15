import { createAdminClient } from "@/lib/supabase/admin";

const MIN_ELIGIBLE_SPEND_CENTS = 799;

export interface EligibilityResult {
  eligible: boolean;
  totalSpentCents: number;
  remainingCents: number;
}

export async function checkReferralEligibility(userId: string): Promise<EligibilityResult> {
  const supabase = createAdminClient();

  // Sum purchase-type credit transactions (Stripe credit purchases + USDC)
  const { data: transactions } = await supabase
    .from("credit_transactions")
    .select("amount_cents")
    .eq("user_id", userId)
    .in("type", ["purchase", "usdc_purchase"]);

  let totalSpentCents = 0;

  if (transactions) {
    totalSpentCents += transactions.reduce((sum, tx) => sum + tx.amount_cents, 0);
  }

  // Also count subscription payments
  const { data: sub } = await supabase
    .from("subscriptions")
    .select("plan, created_at, status")
    .eq("user_id", userId)
    .single();

  if (sub && sub.status !== "canceled") {
    const planPriceCents = sub.plan === "pro" ? 1499 : 799;
    const monthsSinceCreation = Math.max(1, Math.ceil(
      (Date.now() - new Date(sub.created_at).getTime()) / (30 * 24 * 60 * 60 * 1000)
    ));
    totalSpentCents += planPriceCents * monthsSinceCreation;
  }

  const remainingCents = Math.max(0, MIN_ELIGIBLE_SPEND_CENTS - totalSpentCents);

  return {
    eligible: totalSpentCents >= MIN_ELIGIBLE_SPEND_CENTS,
    totalSpentCents,
    remainingCents,
  };
}
