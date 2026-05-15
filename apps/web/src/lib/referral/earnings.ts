import { createAdminClient } from "@/lib/supabase/admin";
import {
  calculateEarningCents,
  getSettlementPeriodMonth,
} from "@/lib/referral/utils";

type SourceType = "subscription" | "credit_purchase" | "usdc_purchase";

/**
 * Accrue referral earnings when a referee makes a payment.
 * No-op if the user has no referrer.
 */
export async function accrueReferralEarning(
  refereeUserId: string,
  sourceAmountCents: number,
  sourceType: SourceType,
  sourcePaymentId?: string | null,
): Promise<void> {
  if (sourceAmountCents <= 0) return;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const supabase: any = createAdminClient();

  const { data: referral } = await supabase
    .from("referrals")
    .select("referrer_id")
    .eq("referee_id", refereeUserId)
    .single();

  if (!referral) return;

  const earningCents = calculateEarningCents(sourceAmountCents);
  if (earningCents <= 0) return;

  const periodMonth = getSettlementPeriodMonth(new Date());

  const { error } = await supabase.from("referral_earnings").insert({
    referrer_id: referral.referrer_id,
    referee_id: refereeUserId,
    source_type: sourceType,
    source_amount_cents: sourceAmountCents,
    earning_cents: earningCents,
    period_month: periodMonth,
    source_payment_id: sourcePaymentId ?? null,
  });
  if (error && error.code !== "23505") {
    console.error("[referral] accrueReferralEarning failed:", error.message);
  }
}
