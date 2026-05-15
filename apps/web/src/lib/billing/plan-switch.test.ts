import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  calculateRemainingTrialDays,
  calculateProrationCreditCents,
  switchPlan,
} from "./plan-switch";

const mocks = vi.hoisted(() => ({
  supabase: null as ReturnType<typeof createPlanSwitchSupabaseMock> | null,
  reprovisionUserBot: vi.fn(),
}));

vi.mock("@/lib/supabase/admin", () => ({
  createAdminClient: () => {
    if (!mocks.supabase) throw new Error("supabase mock not configured");
    return mocks.supabase;
  },
}));

vi.mock("@/lib/api/stripe", () => ({
  getStripe: () => ({
    subscriptions: {
      retrieve: vi.fn(),
      update: vi.fn(),
    },
  }),
}));

vi.mock("@/lib/provisioning/trigger", () => ({
  reprovisionUserBot: mocks.reprovisionUserBot,
}));

function createPlanSwitchSupabaseMock(subscription: Record<string, unknown>) {
  const state = {
    mutations: [] as Array<{ table: string; action: string; payload: unknown }>,
  };

  const supabase = {
    state,
    from: vi.fn((table: string) => {
      const query = {
        select: vi.fn(() => query),
        eq: vi.fn(() => query),
        in: vi.fn(async () => ({ error: null })),
        single: vi.fn(async () => ({
          data: table === "subscriptions" ? subscription : null,
          error: null,
        })),
        update: vi.fn((payload: unknown) => {
          state.mutations.push({ table, action: "update", payload });
          return query;
        }),
        insert: vi.fn(async (payload: unknown) => {
          state.mutations.push({ table, action: "insert", payload });
          return { error: null };
        }),
      };
      return query;
    }),
    rpc: vi.fn(async () => ({ data: true, error: null })),
  };

  return supabase;
}

describe("calculateRemainingTrialDays", () => {
  // Shared trial is 7 days. Implementation: Math.ceil(7 - elapsedDays).
  it("returns 7 days when trial just started", () => {
    const now = new Date().toISOString();
    expect(calculateRemainingTrialDays(now)).toBe(7);
  });

  it("returns 5 days after 2 days elapsed", () => {
    const twoDaysAgo = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString();
    expect(calculateRemainingTrialDays(twoDaysAgo)).toBe(5);
  });

  it("returns 0 when trial is expired (after 8 days)", () => {
    const past = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
    expect(calculateRemainingTrialDays(past)).toBe(0);
  });

  it("returns 1 when 6 days and a few hours have elapsed", () => {
    const elapsed = 6.5 * 24 * 60 * 60 * 1000;
    const start = new Date(Date.now() - elapsed).toISOString();
    expect(calculateRemainingTrialDays(start)).toBe(1);
  });
});

describe("calculateProrationCreditCents", () => {
  const BYOK_PRICE = 799;

  it("returns 0 when period is over", () => {
    const pastEnd = Math.floor(Date.now() / 1000) - 1000;
    const pastStart = pastEnd - 30 * 86400;
    expect(calculateProrationCreditCents(pastStart, pastEnd, BYOK_PRICE)).toBe(0);
  });

  it("returns approximately half for mid-period", () => {
    const now = Math.floor(Date.now() / 1000);
    const periodStart = now - 15 * 86400;
    const periodEnd = now + 15 * 86400;
    const credit = calculateProrationCreditCents(periodStart, periodEnd, BYOK_PRICE);
    // $7.99 * ~50% = ~$4.00 = ~400 cents (allow some rounding tolerance)
    expect(credit).toBeGreaterThan(370);
    expect(credit).toBeLessThan(430);
  });

  it("returns full amount at start of period", () => {
    const now = Math.floor(Date.now() / 1000);
    const periodStart = now;
    const periodEnd = now + 30 * 86400;
    const credit = calculateProrationCreditCents(periodStart, periodEnd, BYOK_PRICE);
    // Should be close to $7.99 = 799 cents
    expect(credit).toBeGreaterThan(770);
    expect(credit).toBeLessThanOrEqual(799);
  });

  it("returns 0 when period length is zero", () => {
    const now = Math.floor(Date.now() / 1000);
    expect(calculateProrationCreditCents(now, now, BYOK_PRICE)).toBe(0);
  });

  it("scales correctly with a different plan price", () => {
    const now = Math.floor(Date.now() / 1000);
    const periodStart = now;
    const periodEnd = now + 30 * 86400;
    const credit = calculateProrationCreditCents(periodStart, periodEnd, 1499);
    // Should be close to $14.99 = 1499 cents
    expect(credit).toBeGreaterThan(1450);
    expect(credit).toBeLessThanOrEqual(1499);
  });
});

describe("switchPlan", () => {
  beforeEach(() => {
    mocks.reprovisionUserBot.mockReset();
    mocks.supabase = createPlanSwitchSupabaseMock({
      user_id: "user-1",
      plan: "byok",
      status: "trialing",
      stripe_subscription_id: null,
      trial_started_at: new Date().toISOString(),
    });
  });

  it("requires checkout before escalating a no-Stripe trial to a platform-credit plan", async () => {
    await expect(
      switchPlan({ userId: "user-1", targetPlan: "pro" }),
    ).rejects.toThrow(/checkout/i);

    expect(mocks.supabase?.state.mutations).toEqual([]);
    expect(mocks.reprovisionUserBot).not.toHaveBeenCalled();
  });
});
