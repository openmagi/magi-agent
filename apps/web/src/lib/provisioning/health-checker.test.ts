import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { checkBotHealth } from "./health-checker";
import type { K8sClient } from "./k8s-client";

type K8sHealthClient = Pick<K8sClient, "getPodStatus" | "getPodLogs" | "areContainersReady">;

describe("health-checker", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Helper: create mock that returns node-host logs indicating gateway ready */
  function makeMockK8s(
    overrides: Partial<{
      getPodStatus: ReturnType<typeof vi.fn>;
      getPodLogs: ReturnType<typeof vi.fn>;
      areContainersReady: ReturnType<typeof vi.fn>;
    }> = {},
  ): K8sHealthClient {
    return {
      getPodStatus: overrides.getPodStatus ?? vi.fn().mockResolvedValue("Running"),
      getPodLogs:
        overrides.getPodLogs ??
        vi.fn().mockImplementation(
          (_ns: string, _pod: string, container?: string) => {
            if (container === "node-host") {
              return Promise.resolve("node host PATH: /some/path");
            }
            if (container === "gateway") {
              return Promise.resolve("gateway started listening telegram polling");
            }
            return Promise.resolve("gateway started");
          },
        ),
      areContainersReady: overrides.areContainersReady ?? vi.fn().mockResolvedValue(true),
    };
  }

  it("returns healthy when pod is Running and gateway ready", async () => {
    const mockK8s = makeMockK8s();

    const resultPromise = checkBotHealth(mockK8s, "ns", "pod");
    // Advance past gateway ready delay (in case first attempt doesn't match)
    await vi.advanceTimersByTimeAsync(30_000);

    const result = await resultPromise;
    expect(result.healthy).toBe(true);
    expect(result.status).toBe("Running");
  });

  it("retries on Pending status", async () => {
    const mockK8s = makeMockK8s({
      getPodStatus: vi
        .fn()
        .mockResolvedValueOnce("Pending")
        .mockResolvedValueOnce("Pending")
        .mockResolvedValueOnce("Running"),
    });

    const resultPromise = checkBotHealth(mockK8s, "ns", "pod", {
      retries: 3,
      delayMs: 10,
    });
    await vi.advanceTimersByTimeAsync(60_000);

    const result = await resultPromise;
    expect(result.healthy).toBe(true);
    expect(mockK8s.getPodStatus).toHaveBeenCalledTimes(3);
  });

  it("returns unhealthy after max retries", async () => {
    const mockK8s = makeMockK8s({
      getPodStatus: vi.fn().mockResolvedValue("Pending"),
    });

    const resultPromise = checkBotHealth(mockK8s, "ns", "pod", {
      retries: 3,
      delayMs: 10,
    });
    await vi.advanceTimersByTimeAsync(60_000);

    const result = await resultPromise;
    expect(result.healthy).toBe(false);
    expect(result.status).toBe("Pending");
  });

  it("detects provider cooldown", async () => {
    const mockK8s = makeMockK8s({
      getPodLogs: vi.fn().mockImplementation(
        (_ns: string, _pod: string, container?: string) => {
          if (container === "node-host") {
            return Promise.resolve("node host PATH: /some/path");
          }
          return Promise.resolve("error: provider disabled until 2026-02-24");
        },
      ),
    });

    const resultPromise = checkBotHealth(mockK8s, "ns", "pod");
    await vi.advanceTimersByTimeAsync(60_000);

    const result = await resultPromise;
    expect(result.healthy).toBe(false);
    expect(result.details).toContain("cooldown");
  });

  it("returns unhealthy on Failed status without retrying", async () => {
    const mockK8s = makeMockK8s({
      getPodStatus: vi.fn().mockResolvedValue("Failed"),
    });

    const resultPromise = checkBotHealth(mockK8s, "ns", "pod", {
      retries: 3,
      delayMs: 10,
    });
    await vi.advanceTimersByTimeAsync(60_000);

    const result = await resultPromise;
    expect(result.healthy).toBe(false);
    expect(result.status).toBe("Failed");
    // Should not retry on terminal failure
    expect(mockK8s.getPodStatus).toHaveBeenCalledTimes(1);
  });

  it("retries on getPodStatus errors", async () => {
    const mockK8s = makeMockK8s({
      getPodStatus: vi
        .fn()
        .mockRejectedValueOnce(new Error("connection refused"))
        .mockResolvedValueOnce("Running"),
    });

    const resultPromise = checkBotHealth(mockK8s, "ns", "pod", {
      retries: 3,
      delayMs: 10,
    });
    await vi.advanceTimersByTimeAsync(60_000);

    const result = await resultPromise;
    expect(result.healthy).toBe(true);
    expect(mockK8s.getPodStatus).toHaveBeenCalledTimes(2);
  });

  it("returns unhealthy when all retries fail with errors", async () => {
    const mockK8s = makeMockK8s({
      getPodStatus: vi
        .fn()
        .mockRejectedValue(new Error("connection refused")),
    });

    const resultPromise = checkBotHealth(mockK8s, "ns", "pod", {
      retries: 2,
      delayMs: 10,
    });
    await vi.advanceTimersByTimeAsync(60_000);

    const result = await resultPromise;
    expect(result.healthy).toBe(false);
    expect(result.details).toContain("connection refused");
  });

  it("returns unhealthy when gateway never becomes ready", async () => {
    const mockK8s = makeMockK8s({
      getPodLogs: vi.fn().mockImplementation(
        (_ns: string, _pod: string, container?: string) => {
          if (container === "node-host") {
            // node-host keeps failing to connect
            return Promise.resolve("connect failed: ECONNREFUSED 127.0.0.1:8080");
          }
          return Promise.resolve("gateway started");
        },
      ),
    });

    const resultPromise = checkBotHealth(mockK8s, "ns", "pod");
    // Advance past all gateway retry delays (36 retries × 5s = 180s)
    await vi.advanceTimersByTimeAsync(200_000);

    const result = await resultPromise;
    expect(result.healthy).toBe(false);
    expect(result.status).toBe("Running");
    expect(result.details).toContain("Gateway did not become ready");
  });

  it("waits for gateway to become ready after initial failures", async () => {
    let nodeHostCallCount = 0;
    const mockK8s = makeMockK8s({
      getPodLogs: vi.fn().mockImplementation(
        (_ns: string, _pod: string, container?: string) => {
          if (container === "node-host") {
            nodeHostCallCount++;
            // First 2 calls: connect failed, then success
            if (nodeHostCallCount <= 2) {
              return Promise.resolve("connect failed: ECONNREFUSED 127.0.0.1:8080");
            }
            return Promise.resolve("node host PATH: /some/path");
          }
          if (container === "gateway") {
            return Promise.resolve("gateway started listening telegram polling");
          }
          return Promise.resolve("gateway started");
        },
      ),
    });

    const resultPromise = checkBotHealth(mockK8s, "ns", "pod");
    await vi.advanceTimersByTimeAsync(120_000);

    const result = await resultPromise;
    expect(result.healthy).toBe(true);
    expect(result.status).toBe("Running");
    expect(nodeHostCallCount).toBe(3);
  });

  it("returns unhealthy when gateway is connected but not processing Telegram", async () => {
    const mockK8s = makeMockK8s({
      getPodLogs: vi.fn().mockImplementation(
        (_ns: string, _pod: string, container?: string) => {
          if (container === "node-host") {
            return Promise.resolve("node host PATH: /some/path");
          }
          if (container === "gateway") {
            // Gateway started but no Telegram patterns
            return Promise.resolve("gateway starting up...");
          }
          return Promise.resolve("");
        },
      ),
      areContainersReady: vi.fn().mockResolvedValue(false),
    });

    const resultPromise = checkBotHealth(mockK8s, "ns", "pod");
    // Advance past gateway ready + gateway live retries
    await vi.advanceTimersByTimeAsync(300_000);

    const result = await resultPromise;
    expect(result.healthy).toBe(false);
    expect(result.details).toContain("not yet processing Telegram");
  });
});
