import Stripe from "stripe";
import { createAdminClient } from "@/lib/supabase/admin";

/**
 * Bind a referral at signup time. Inserts into `referrals` with stripe_coupon_id = NULL.
 * Best-effort: catches all errors so referral failure never blocks bot creation.
 */
export async function bindReferral(
  refereeId: string,
  referralCode: string,
): Promise<void> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const supabase: any = createAdminClient();

  try {
    // Look up the referral code
    const { data: codeRecord } = await supabase
      .from("referral_codes")
      .select("id, user_id, code")
      .eq("code", referralCode.toUpperCase())
      .single();

    if (!codeRecord) return;

    // Block self-referral
    if (codeRecord.user_id === refereeId) return;

    // Block duplicate — referee_id has UNIQUE constraint
    const { data: existingRef } = await supabase
      .from("referrals")
      .select("id")
      .eq("referee_id", refereeId)
      .single();

    if (existingRef) return;

    // Insert binding with no coupon yet
    await supabase.from("referrals").insert({
      referrer_id: codeRecord.user_id,
      referee_id: refereeId,
      referral_code_id: codeRecord.id,
      stripe_coupon_id: null,
    });
  } catch (err) {
    console.error("[referral] bindReferral failed (non-blocking):", err);
  }
}

/**
 * Apply referral discount at subscription checkout.
 * Looks up an existing binding (stripe_coupon_id IS NULL) and creates a Stripe coupon.
 * Returns the coupon ID if applicable, null otherwise.
 */
export async function applyReferralToCheckout(
  stripe: Stripe,
  userId: string,
): Promise<string | null> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const supabase: any = createAdminClient();

  try {
    // Find an unactivated referral binding for this user
    const { data: referral } = await supabase
      .from("referrals")
      .select("id, referrer_id, referral_code_id")
      .eq("referee_id", userId)
      .is("stripe_coupon_id", null)
      .single();

    if (!referral) return null;

    // Look up the referral code for the coupon name
    const { data: codeRecord } = await supabase
      .from("referral_codes")
      .select("code")
      .eq("id", referral.referral_code_id)
      .single();

    // Create a one-time 50% off coupon
    const coupon = await stripe.coupons.create({
      percent_off: 50,
      duration: "once",
      name: `Referral: ${codeRecord?.code ?? "unknown"}`,
      metadata: {
        referral_code_id: referral.referral_code_id,
        referrer_id: referral.referrer_id,
        referee_id: userId,
      },
    });

    // Update the binding with the coupon ID
    await supabase
      .from("referrals")
      .update({ stripe_coupon_id: coupon.id })
      .eq("id", referral.id);

    return coupon.id;
  } catch (err) {
    console.error("[referral] applyReferralToCheckout failed:", err);
    return null;
  }
}

export async function prepareReferralCheckoutDiscounts(
  stripe: Stripe,
  userId: string,
  referralCode?: string | null,
): Promise<{ coupon: string }[]> {
  if (referralCode) {
    await bindReferral(userId, referralCode);
  }

  const referralCouponId = await applyReferralToCheckout(stripe, userId);
  return referralCouponId ? [{ coupon: referralCouponId }] : [];
}

export async function recordReferral(
  referrerId: string,
  refereeId: string,
  referralCodeId: string,
  stripeCouponId: string,
): Promise<void> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const supabase: any = createAdminClient();

  await supabase.from("referrals").insert({
    referrer_id: referrerId,
    referee_id: refereeId,
    referral_code_id: referralCodeId,
    stripe_coupon_id: stripeCouponId,
  });
}
