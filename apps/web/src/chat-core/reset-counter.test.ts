import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildResetDivider,
  buildResetSessionKey,
  getResetBoundaryTimestamp,
  getResetCounter,
  incrementResetCounter,
  syncResetCounters,
} from "./reset-counter";

// jsdom is not installed; stub localStorage with an in-memory map (same pattern
// as channel-model-selection.test.ts). Clear between tests so counters don't leak.
const storage = new Map<string, string>();
beforeEach(() => {
  storage.clear();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storage.set(key, value);
    },
    removeItem: (key: string) => {
      storage.delete(key);
    },
    clear: () => {
      storage.clear();
    },
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("buildResetSessionKey", () => {
  it("omits the counter suffix when rc === 0", () => {
    expect(buildResetSessionKey("main", 0)).toBe("agent:main:app:main");
  });

  it("appends `:rc` when rc > 0", () => {
    expect(buildResetSessionKey("main", 2)).toBe("agent:main:app:main:2");
    expect(buildResetSessionKey("general", 1)).toBe("agent:main:app:general:1");
  });
});

describe("buildResetDivider", () => {
  it("returns a system divider message keeping history (no delete)", () => {
    const divider = buildResetDivider(1234);
    expect(divider).toEqual({
      id: "system-reset-1234",
      role: "system",
      content: "Session ended — new conversation started",
      timestamp: 1234,
    });
  });
});

describe("incrementResetCounter", () => {
  it("optimistically bumps localStorage and returns the new value", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ resetCount: 1 }),
    } as Response);

    expect(getResetCounter("bot1", "main")).toBe(0);
    const next = await incrementResetCounter({
      botId: "bot1",
      channel: "main",
      token: "tok",
      fetchImpl,
    });
    expect(next).toBe(1);
    expect(getResetCounter("bot1", "main")).toBe(1);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("records a local reset boundary before the server round trip completes", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-12T12:00:00.000Z"));
    try {
      let resolveToken: (token: string | null) => void = () => {};
      const tokenPromise = new Promise<string | null>((resolve) => {
        resolveToken = resolve;
      });
      const fetchImpl = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ resetCount: 1 }),
      } as Response);

      const nextPromise = incrementResetCounter({
        botId: "bot1",
        channel: "main",
        getToken: () => tokenPromise,
        fetchImpl,
      });

      expect(getResetCounter("bot1", "main")).toBe(1);
      expect(getResetBoundaryTimestamp("bot1", "main")).toBe(
        Date.parse("2026-06-12T12:00:00.000Z"),
      );

      resolveToken("tok");
      await expect(nextPromise).resolves.toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("adopts a higher server counter", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ resetCount: 5 }),
    } as Response);
    const next = await incrementResetCounter({
      botId: "bot1",
      channel: "main",
      token: "tok",
      fetchImpl,
    });
    expect(next).toBe(5);
    expect(getResetCounter("bot1", "main")).toBe(5);
  });

  it("adopts a newer server reset boundary for the same counter value", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-12T12:00:00.000Z"));
    try {
      const serverResetAt = Date.parse("2026-06-12T12:00:05.000Z");
      const fetchImpl = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ resetCount: 1, resetAt: serverResetAt }),
      } as Response);

      const next = await incrementResetCounter({
        botId: "bot1",
        channel: "main",
        token: "tok",
        fetchImpl,
      });

      expect(next).toBe(1);
      expect(getResetBoundaryTimestamp("bot1", "main")).toBe(serverResetAt);
    } finally {
      vi.useRealTimers();
    }
  });

  it("still bumps locally without a token (no POST)", async () => {
    const fetchImpl = vi.fn();
    const next = await incrementResetCounter({
      botId: "bot1",
      channel: "main",
      token: null,
      fetchImpl,
    });
    expect(next).toBe(1);
    expect(getResetCounter("bot1", "main")).toBe(1);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("bumps localStorage before waiting for async token lookup", async () => {
    let resolveToken: (token: string | null) => void = () => {};
    const tokenPromise = new Promise<string | null>((resolve) => {
      resolveToken = resolve;
    });
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ resetCount: 1 }),
    } as Response);

    const nextPromise = incrementResetCounter({
      botId: "bot1",
      channel: "main",
      getToken: () => tokenPromise,
      fetchImpl,
    });

    expect(getResetCounter("bot1", "main")).toBe(1);
    expect(fetchImpl).not.toHaveBeenCalled();

    resolveToken("tok");

    await expect(nextPromise).resolves.toBe(1);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });
});

describe("syncResetCounters", () => {
  it("reads legacy numeric counters without inventing a reset boundary", () => {
    localStorage.setItem(
      "clawy:resetCounters:bot1",
      JSON.stringify({ main: 2 }),
    );

    expect(getResetCounter("bot1", "main")).toBe(2);
    expect(getResetBoundaryTimestamp("bot1", "main")).toBeNull();
  });

  it("merges higher server counters into localStorage without lowering local values", async () => {
    localStorage.setItem(
      "clawy:resetCounters:bot1",
      JSON.stringify({
        main: { count: 2, updatedAt: 2_000 },
        localOnly: { count: 4, updatedAt: 4_000 },
      }),
    );
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        counters: {
          main: 5,
          localOnly: 1,
          other: 3,
        },
        resetAt: {
          main: 5_000,
          localOnly: 1_000,
          other: 3_000,
        },
      }),
    } as Response);

    await syncResetCounters({
      botId: "bot1",
      getToken: async () => "tok",
      fetchImpl,
    });

    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/chat/reset-counters?botId=bot1",
      { headers: { Authorization: "Bearer tok" } },
    );
    expect(getResetCounter("bot1", "main")).toBe(5);
    expect(getResetCounter("bot1", "localOnly")).toBe(4);
    expect(getResetCounter("bot1", "other")).toBe(3);
    expect(getResetBoundaryTimestamp("bot1", "main")).toBe(5_000);
    expect(getResetBoundaryTimestamp("bot1", "localOnly")).toBe(4_000);
    expect(getResetBoundaryTimestamp("bot1", "other")).toBe(3_000);
  });
});
