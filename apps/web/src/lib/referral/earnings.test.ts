import { beforeEach, describe, expect, it, vi } from "vitest";
import { accrueReferralEarning } from "./earnings";

const mocks = vi.hoisted(() => ({
  referral: { referrer_id: "referrer-1" } as { referrer_id: string } | null,
  inserts: [] as unknown[],
  insertError: null as { code?: string; message: string } | null,
}));

vi.mock("@/lib/supabase/admin", () => ({
  createAdminClient: () => ({
    from: (table: string) => {
      const query = {
        select: () => query,
        eq: () => query,
        single: async () => ({
          data: table === "referrals" ? mocks.referral : null,
          error: null,
        }),
        insert: async (payload: unknown) => {
          mocks.inserts.push(payload);
          return { data: null, error: mocks.insertError };
        },
      };
      return query;
    },
  }),
}));

describe("referral earnings", () => {
  beforeEach(() => {
    mocks.referral = { referrer_id: "referrer-1" };
    mocks.inserts = [];
    mocks.insertError = null;
  });

  it("stores source payment identity so payment retries cannot double-accrue", async () => {
    await accrueReferralEarning("referee-1", 2500, "subscription", "in_123");

    expect(mocks.inserts).toEqual([
      expect.objectContaining({
        referrer_id: "referrer-1",
        referee_id: "referee-1",
        source_type: "subscription",
        source_amount_cents: 2500,
        earning_cents: 250,
        source_payment_id: "in_123",
      }),
    ]);
  });

  it("treats duplicate payment identities as already accrued", async () => {
    mocks.insertError = { code: "23505", message: "duplicate key" };

    await expect(
      accrueReferralEarning("referee-1", 2500, "subscription", "in_123"),
    ).resolves.toBeUndefined();
  });
});
