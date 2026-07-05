import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildSessionKey,
  fetchChannelMessages,
  getActiveSnapshot,
  setChatTokenGetter,
} from "./chat-client";

/**
 * Tests for the LOCAL-serve variants of the refresh-resume readers. When the
 * two `isLocalBot` short-circuits are removed, `getActiveSnapshot` and
 * `fetchChannelMessages` must route local bots to the serve endpoints
 * (`/v1/chat/active-snapshot?sessionId=` and `/v1/chat/channel-messages?sessionId=`)
 * keyed by the reset-aware session id, and map the serve payloads onto the
 * shapes the UI consumes. All "nothing to resume" paths return null / [].
 */

const originalFetch = globalThis.fetch;
const LOCAL = "local";
const CHANNEL = "general";

function mockFetch(status: number, body: unknown): ReturnType<typeof vi.fn> {
  const impl = vi.fn(async () => {
    const bodyStr = typeof body === "string" ? body : JSON.stringify(body);
    return new Response(bodyStr, {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  globalThis.fetch = impl as unknown as typeof globalThis.fetch;
  return impl;
}

beforeEach(() => {
  setChatTokenGetter(async () => "loopback-token");
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("getActiveSnapshot (local)", () => {
  it("routes to the serve active-snapshot endpoint with the reset-aware sessionId", async () => {
    const fetchMock = mockFetch(200, {
      snapshot: { turnId: "t1", status: "running", content: "live" },
    });
    const snap = await getActiveSnapshot(LOCAL, CHANNEL);
    expect(snap).toEqual({ turnId: "t1", status: "running", content: "live" });
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    const sessionId = buildSessionKey(LOCAL, CHANNEL);
    expect(url).toContain("/v1/chat/active-snapshot?sessionId=");
    expect(url).toContain(encodeURIComponent(sessionId));
  });

  it("attaches the loopback bearer token", async () => {
    const fetchMock = mockFetch(200, { snapshot: null });
    await getActiveSnapshot(LOCAL, CHANNEL);
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(opts?.headers);
    expect(headers.get("Authorization")).toBe("Bearer loopback-token");
  });

  it("returns null when the server reports no live snapshot", async () => {
    mockFetch(200, { snapshot: null });
    expect(await getActiveSnapshot(LOCAL, CHANNEL)).toBeNull();
  });

  it("returns null on a non-ok response", async () => {
    mockFetch(503, { error: "streaming_chat_disabled" });
    expect(await getActiveSnapshot(LOCAL, CHANNEL)).toBeNull();
  });

  it("returns null (never throws) on a network failure", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    }) as unknown as typeof globalThis.fetch;
    expect(await getActiveSnapshot(LOCAL, CHANNEL)).toBeNull();
  });
});

describe("fetchChannelMessages (local)", () => {
  it("routes to the serve channel-messages endpoint and maps the payload", async () => {
    const fetchMock = mockFetch(200, {
      messages: [
        { role: "assistant", content: "The answer is 42.", createdAt: 1_000_000, turnId: "t-int" },
      ],
    });
    const msgs = await fetchChannelMessages(LOCAL, CHANNEL);
    expect(msgs).toHaveLength(1);
    expect(msgs[0].role).toBe("assistant");
    expect(msgs[0].content).toBe("The answer is 42.");
    expect(msgs[0].id).toBe("local-turn-t-int");
    expect(typeof msgs[0].created_at).toBe("string");
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/chat/channel-messages?sessionId=");
    expect(url).toContain(encodeURIComponent(buildSessionKey(LOCAL, CHANNEL)));
  });

  it("filters out empty-content entries", async () => {
    mockFetch(200, {
      messages: [
        { role: "assistant", content: "", createdAt: 1, turnId: "t0" },
        { role: "assistant", content: "kept", createdAt: 2, turnId: "t1" },
      ],
    });
    const msgs = await fetchChannelMessages(LOCAL, CHANNEL);
    expect(msgs).toHaveLength(1);
    expect(msgs[0].content).toBe("kept");
  });

  it("returns [] when the server has nothing to rehydrate", async () => {
    mockFetch(200, { messages: [] });
    expect(await fetchChannelMessages(LOCAL, CHANNEL)).toEqual([]);
  });

  it("returns [] on a non-ok response", async () => {
    mockFetch(400, { error: "missing_session_id" });
    expect(await fetchChannelMessages(LOCAL, CHANNEL)).toEqual([]);
  });

  it("returns [] (never throws) on a network failure", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("boom");
    }) as unknown as typeof globalThis.fetch;
    expect(await fetchChannelMessages(LOCAL, CHANNEL)).toEqual([]);
  });
});
