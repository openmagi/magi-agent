import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  supabase: null as unknown as MockSupabase,
  stripe: {
    subscriptions: {
      list: vi.fn(),
      cancel: vi.fn(),
    },
    customers: {
      del: vi.fn(),
    },
  },
  deleteBotAndCleanup: vi.fn(),
  captureServerEvent: vi.fn(),
  privyDeleteUser: vi.fn(),
}));

vi.mock("@/lib/supabase/admin", () => ({
  createAdminClient: () => mocks.supabase,
}));

vi.mock("@/lib/api/stripe", () => ({
  getStripe: () => mocks.stripe,
}));

vi.mock("@/lib/services/bot-service", () => ({
  deleteBotAndCleanup: mocks.deleteBotAndCleanup,
}));

vi.mock("@/lib/posthog/server", () => ({
  captureServerEvent: mocks.captureServerEvent,
}));

vi.mock("@/lib/config", () => ({
  env: {
    NEXT_PUBLIC_PRIVY_APP_ID: "privy-app",
    PRIVY_APP_SECRET: "privy-secret",
  },
}));

vi.mock("@privy-io/server-auth", () => ({
  PrivyClient: vi.fn().mockImplementation(function PrivyClient() {
    return { deleteUser: mocks.privyDeleteUser };
  }),
}));

interface Row {
  [key: string]: unknown;
}

interface MockState {
  rows: Record<string, Row[]>;
  deletes: Array<{ table: string; filters: Filter[] }>;
}

interface Filter {
  op: "eq" | "neq" | "in";
  column: string;
  value: unknown;
}

class MockQuery {
  private action: "select" | "delete" | null = null;
  private filters: Filter[] = [];

  constructor(
    private readonly state: MockState,
    private readonly table: string,
  ) {}

  select() {
    this.action = "select";
    return this;
  }

  delete() {
    this.action = "delete";
    return this;
  }

  eq(column: string, value: unknown) {
    this.filters.push({ op: "eq", column, value });
    return this;
  }

  neq(column: string, value: unknown) {
    this.filters.push({ op: "neq", column, value });
    return this;
  }

  in(column: string, value: unknown[]) {
    this.filters.push({ op: "in", column, value });
    return this;
  }

  single() {
    return this.execute(true);
  }

  maybeSingle() {
    return this.execute(true);
  }

  then<TResult1 = unknown, TResult2 = never>(
    onfulfilled?: ((value: unknown) => TResult1 | PromiseLike<TResult1>) | null,
    onrejected?: ((reason: unknown) => TResult2 | PromiseLike<TResult2>) | null,
  ) {
    return this.execute(false).then(onfulfilled, onrejected);
  }

  private execute(single: boolean) {
    if (this.action === "delete") {
      this.state.deletes.push({ table: this.table, filters: [...this.filters] });
      return Promise.resolve({ error: null });
    }

    const rows = (this.state.rows[this.table] ?? []).filter((row) =>
      this.filters.every((filter) => {
        const value = row[filter.column];
        if (filter.op === "eq") return value === filter.value;
        if (filter.op === "neq") return value !== filter.value;
        return Array.isArray(filter.value) && filter.value.includes(value);
      }),
    );

    return Promise.resolve({
      data: single ? rows[0] ?? null : rows,
      error: null,
    });
  }
}

class MockSupabase {
  readonly state: MockState;

  constructor(rows: Record<string, Row[]>) {
    this.state = { rows, deletes: [] };
  }

  from(table: string) {
    return new MockQuery(this.state, table);
  }
}

describe("deleteAccount", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    mocks.stripe.subscriptions.list.mockResolvedValue({ data: [], has_more: false });
    mocks.stripe.customers.del.mockResolvedValue({ deleted: true });
    mocks.deleteBotAndCleanup.mockResolvedValue(undefined);
    mocks.privyDeleteUser.mockResolvedValue(undefined);
  });

  it("cleans up every user bot, including already-deleted tombstones", async () => {
    mocks.supabase = new MockSupabase({
      bots: [
        { id: "active-bot", user_id: "user-1", status: "active" },
        { id: "deleted-bot", user_id: "user-1", status: "deleted" },
      ],
      profiles: [{ id: "user-1", stripe_customer_id: null }],
    });
    const { deleteAccount } = await import("./account-service");

    await deleteAccount("user-1");

    expect(mocks.deleteBotAndCleanup).toHaveBeenCalledWith("active-bot");
    expect(mocks.deleteBotAndCleanup).toHaveBeenCalledWith("deleted-bot");
  });

  it("fails before deleting profile rows when bot cleanup fails", async () => {
    mocks.supabase = new MockSupabase({
      bots: [{ id: "bot-1", user_id: "user-1", status: "active" }],
      profiles: [{ id: "user-1", stripe_customer_id: null }],
    });
    mocks.deleteBotAndCleanup.mockRejectedValueOnce(new Error("namespace stuck"));
    const { deleteAccount } = await import("./account-service");

    await expect(deleteAccount("user-1")).rejects.toThrow("Bot cleanup failed");

    expect(mocks.supabase.state.deletes).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ table: "profiles" })]),
    );
  });

  it("fails before deleting profile rows when Stripe customer cleanup fails", async () => {
    mocks.supabase = new MockSupabase({
      bots: [],
      profiles: [{ id: "user-1", stripe_customer_id: "cus_123" }],
    });
    mocks.stripe.customers.del.mockRejectedValueOnce(new Error("stripe unavailable"));
    const { deleteAccount } = await import("./account-service");

    await expect(deleteAccount("user-1")).rejects.toThrow("Stripe cleanup failed");

    expect(mocks.supabase.state.deletes).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ table: "profiles" })]),
    );
  });

  it("deletes user-level non-cascade account tables before profile deletion", async () => {
    mocks.supabase = new MockSupabase({
      bots: [],
      profiles: [{ id: "user-1", stripe_customer_id: null }],
    });
    const { deleteAccount } = await import("./account-service");

    await deleteAccount("user-1");

    expect(mocks.supabase.state.deletes.map((call) => call.table)).toEqual(
      expect.arrayContaining([
        "subscriptions",
        "credits",
        "email_quotas",
        "search_quotas",
        "credit_grants",
        "search_usage",
        "email_usage",
        "analytics_daily",
        "user_interactions",
        "skill_executions",
        "profiles",
      ]),
    );
  });

  it("does not report success when Privy user deletion fails", async () => {
    mocks.supabase = new MockSupabase({
      bots: [],
      profiles: [{ id: "user-1", stripe_customer_id: null }],
    });
    mocks.privyDeleteUser.mockRejectedValueOnce(new Error("privy unavailable"));
    const { deleteAccount } = await import("./account-service");

    await expect(deleteAccount("user-1")).rejects.toThrow("Privy user delete failed");

    expect(mocks.supabase.state.deletes).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ table: "profiles" })]),
    );
  });
});
