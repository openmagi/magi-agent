import { beforeEach, describe, expect, it, vi } from "vitest";
import Stripe from "stripe";
import {
  bindReferral,
  prepareReferralCheckoutDiscounts,
} from "./checkout";

const mocks = vi.hoisted(() => ({
  state: {
    code: {
      id: "ref-code-1",
      user_id: "referrer-1",
      code: "REF-TESTCODE",
    },
    referral: null as {
      id: string;
      referrer_id: string;
      referee_id: string;
      referral_code_id: string;
      stripe_coupon_id: string | null;
    } | null,
  },
}));

vi.mock("@/lib/supabase/admin", () => ({
  createAdminClient: () => createReferralSupabaseMock(),
}));

function createReferralSupabaseMock() {
  return {
    from: (table: string) => {
      const query = {
        select: () => query,
        eq: () => query,
        is: () => query,
        single: async () => {
          if (table === "referral_codes") {
            return { data: mocks.state.code, error: null };
          }
          if (table === "referrals") {
            return { data: mocks.state.referral, error: null };
          }
          return { data: null, error: null };
        },
        insert: async (payload: {
          referrer_id: string;
          referee_id: string;
          referral_code_id: string;
          stripe_coupon_id: string | null;
        }) => {
          mocks.state.referral = {
            id: "referral-1",
            referrer_id: payload.referrer_id,
            referee_id: payload.referee_id,
            referral_code_id: payload.referral_code_id,
            stripe_coupon_id: payload.stripe_coupon_id,
          };
          return { data: null, error: null };
        },
        update: (payload: { stripe_coupon_id?: string }) => ({
          eq: async () => {
            if (mocks.state.referral && payload.stripe_coupon_id) {
              mocks.state.referral.stripe_coupon_id = payload.stripe_coupon_id;
            }
            return { data: null, error: null };
          },
        }),
      };
      return query;
    },
  };
}

describe("referral checkout", () => {
  beforeEach(() => {
    mocks.state.referral = null;
  });

  it("binds a referral before creating checkout discounts for a first checkout", async () => {
    const stripe = {
      coupons: {
        create: vi.fn(async () => ({ id: "coupon_50" })),
      },
    } as unknown as Stripe;

    const discounts = await prepareReferralCheckoutDiscounts(
      stripe,
      "referee-1",
      "ref-testcode",
    );

    expect(discounts).toEqual([{ coupon: "coupon_50" }]);
    expect(mocks.state.referral).toMatchObject({
      referrer_id: "referrer-1",
      referee_id: "referee-1",
      referral_code_id: "ref-code-1",
      stripe_coupon_id: "coupon_50",
    });
    expect(stripe.coupons.create).toHaveBeenCalledWith(
      expect.objectContaining({
        percent_off: 50,
        duration: "once",
      }),
    );
  });

  it("does not bind self-referrals", async () => {
    await bindReferral("referrer-1", "REF-TESTCODE");

    expect(mocks.state.referral).toBeNull();
  });
});
