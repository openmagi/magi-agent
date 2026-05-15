import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import type Stripe from "stripe";
import {
  handleCheckoutCompleted,
  handleInvoiceFailed,
} from "./webhook-service";

const mocks = vi.hoisted(() => ({
  supabase: null as ReturnType<typeof createWebhookSupabaseMock> | null,
  stripe: {
    customers: {
      createBalanceTransaction: vi.fn(),
    },
    invoices: {
      pay: vi.fn(),
    },
    subscriptions: {
      retrieve: vi.fn(),
    },
  },
  accrueReferralEarning: vi.fn(),
  captureServerEvent: vi.fn(),
}));

vi.mock("@/lib/supabase/admin", () => ({
  createAdminClient: () => {
    if (!mocks.supabase) throw new Error("supabase mock not configured");
    return mocks.supabase;
  },
}));

vi.mock("@/lib/api/stripe", () => ({
  getStripe: () => mocks.stripe,
}));

vi.mock("@/lib/referral/earnings", () => ({
  accrueReferralEarning: mocks.accrueReferralEarning,
}));

vi.mock("@/lib/referral/checkout", () => ({
  recordReferral: vi.fn(),
}));

vi.mock("@/lib/posthog/server", () => ({
  captureServerEvent: mocks.captureServerEvent,
}));

vi.mock("@/lib/billing/plans", () => ({
  PLAN_MONTHLY_CREDITS_CENTS: { pro: 500, pro_plus: 8000 },
  PLAN_SEARCH_QUOTA: { pro: 500, pro_plus: 1000 },
}));

vi.mock("@/lib/billing/plan-switch", () => ({
  executeScheduledSwitch: vi.fn(),
}));

vi.mock("@/lib/crypto", () => ({
  encrypt: (value: string) => `encrypted:${value}`,
}));

vi.mock("@/lib/provisioning/trigger", () => ({
  triggerProvisioning: vi.fn(),
}));

interface WebhookSupabaseState {
  subscription?: Record<string, unknown> | null;
  credits?: Record<string, unknown> | null;
  grant?: Record<string, unknown> | null;
  existingBots?: Array<Record<string, unknown>>;
  newBot?: Record<string, unknown> | null;
  existingRenewal?: Array<Record<string, unknown>>;
  rpcResults?: Record<string, { data: unknown; error: { message: string } | null }>;
}

function createWebhookSupabaseMock(state: WebhookSupabaseState = {}) {
  const calls = {
    updates: [] as Array<{ table: string; payload: unknown }>,
    inserts: [] as Array<{ table: string; payload: unknown }>,
    upserts: [] as Array<{ table: string; payload: unknown }>,
    rpcs: [] as Array<{ name: string; args: unknown }>,
  };

  const supabase = {
    calls,
    from: vi.fn((table: string) => {
      const query = {
        select: vi.fn(() => query),
        eq: vi.fn(() => query),
        in: vi.fn(async () => ({
          data: table === "bots" ? (state.existingBots ?? []) : [],
          error: null,
        })),
        single: vi.fn(async () => {
          if (table === "subscriptions") return { data: state.subscription ?? null, error: null };
          if (table === "credits") return { data: state.credits ?? null, error: null };
          if (table === "credit_grants") return { data: state.grant ?? null, error: null };
          if (table === "bots") return { data: state.newBot ?? { id: "bot-1" }, error: null };
          return { data: null, error: null };
        }),
        limit: vi.fn(async () => ({
          data: table === "credit_transactions" ? (state.existingRenewal ?? []) : [],
          error: null,
        })),
        update: vi.fn((payload: unknown) => {
          calls.updates.push({ table, payload });
          return query;
        }),
        insert: vi.fn((payload: unknown) => {
          calls.inserts.push({ table, payload });
          return query;
        }),
        upsert: vi.fn((payload: unknown) => {
          calls.upserts.push({ table, payload });
          return query;
        }),
      };
      return query;
    }),
    rpc: vi.fn(async (name: string, args: unknown) => {
      calls.rpcs.push({ name, args });
      return state.rpcResults?.[name] ?? { data: true, error: null };
    }),
  };

  return supabase;
}

describe("webhook-service", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    mocks.supabase = createWebhookSupabaseMock();
  });

  it("credits organization top-ups from camelCase Stripe metadata", async () => {
    mocks.supabase = createWebhookSupabaseMock();

    await handleCheckoutCompleted({
      id: "cs_org_1",
      mode: "payment",
      amount_total: 2500,
      payment_intent: "pi_org_1",
      metadata: {
        type: "org_credit_topup",
        userId: "user-1",
        orgId: "org-1",
      },
    } as unknown as Stripe.Checkout.Session);

    expect(mocks.supabase.calls.rpcs).toContainEqual({
      name: "claim_org_stripe_credit",
      args: {
        p_org_id: "org-1",
        p_user_id: "user-1",
        p_stripe_payment_id: "pi_org_1",
        p_amount_cents: 2500,
        p_description: "Organization credit top-up",
      },
    });
    expect(mocks.accrueReferralEarning).not.toHaveBeenCalled();
  });

  it("seeds subscription grants at the hosting-adjusted LLM credit allowance without welcome or SNU bonuses", async () => {
    mocks.supabase = createWebhookSupabaseMock();
    mocks.stripe.subscriptions.retrieve.mockResolvedValue({
      status: "active",
      current_period_end: 1_800_000_000,
    });

    await handleCheckoutCompleted({
      id: "cs_sub_1",
      mode: "subscription",
      customer: "cus_1",
      subscription: "sub_1",
      metadata: {
        user_id: "user-1",
        user_email: "student@snu.ac.kr",
        plan: "pro",
        api_key_mode: "platform_credits",
        model_selection: "sonnet",
      },
    } as unknown as Stripe.Checkout.Session);

    expect(mocks.supabase.calls.upserts).toContainEqual({
      table: "credit_grants",
      payload: { user_id: "user-1", granted_cents: 500, used_cents: 0 },
    });

    const claimedBonuses = mocks.supabase.calls.rpcs.filter(
      (call) => call.name === "claim_stripe_credit"
    );
    expect(claimedBonuses).toEqual([]);
  });

  it("does not keep source paths for legacy welcome or SNU bonus grants", () => {
    const source = readFileSync(new URL("./webhook-service.ts", import.meta.url), "utf8");

    expect(source).not.toContain("PLAN_TRIAL_CREDITS_CENTS");
    expect(source).not.toContain("Welcome trial credits");
    expect(source).not.toContain("SNU Dongmoon");
    expect(source).not.toContain("isSnuEmail");
  });

  it("does not charge Stripe when the final credit reservation would overdraw", async () => {
    mocks.supabase = createWebhookSupabaseMock({
      subscription: { user_id: "user-1", plan: "pro" },
      credits: { balance_cents: 5000 },
      grant: { granted_cents: 0, used_cents: 0 },
      existingRenewal: [],
      rpcResults: {
        check_and_deduct_credits: { data: false, error: null },
      },
    });

    await handleInvoiceFailed({
      id: "in_1",
      customer: "cus_1",
      amount_due: 2000,
      amount_remaining: 2000,
      subscription: "sub_1",
    } as unknown as Stripe.Invoice);

    expect(mocks.supabase.calls.rpcs).toContainEqual({
      name: "check_and_deduct_credits",
      args: { p_user_id: "user-1", p_amount: 2000 },
    });
    expect(mocks.stripe.customers.createBalanceTransaction).not.toHaveBeenCalled();
    expect(mocks.stripe.invoices.pay).not.toHaveBeenCalled();
    expect(mocks.supabase.calls.updates).toContainEqual({
      table: "subscriptions",
      payload: { status: "past_due" },
    });
  });
});
