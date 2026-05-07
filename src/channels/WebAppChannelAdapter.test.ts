import { describe, it, expect } from "vitest";
import { WebAppChannelAdapter } from "./WebAppChannelAdapter.js";
import type { WebAppChannelConfig } from "./WebAppChannelAdapter.js";

interface CapturedCall {
  url: string;
  init: RequestInit | undefined;
}

function makeStubFetch(
  responder: (call: CapturedCall) => { status?: number; body?: string } = () => ({
    status: 202,
    body: JSON.stringify({ id: "row-1", serverId: "srv-1", status: "accepted" }),
  }),
): { fetchImpl: typeof fetch; calls: CapturedCall[] } {
  const calls: CapturedCall[] = [];
  const fetchImpl = (async (
    input: string | URL | Request,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    const call: CapturedCall = { url, init };
    calls.push(call);
    const out = responder(call);
    return new Response(out.body ?? "", {
      status: out.status ?? 202,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  return { fetchImpl, calls };
}

function makeAdapter(overrides: Partial<WebAppChannelConfig> = {}): {
  adapter: WebAppChannelAdapter;
  calls: CapturedCall[];
  fetchImpl: typeof fetch;
} {
  const { fetchImpl, calls } = makeStubFetch(overrides.fetchImpl ? undefined : undefined);
  const adapter = new WebAppChannelAdapter({
    pushEndpointUrl: "https://chat.magi.local/v1/bot-push/message",
    hmacKey: "test-key",
    gatewayToken: "gw-token",
    botId: "bot-1",
    userId: "did:privy:user",
    fetchImpl: overrides.fetchImpl ?? fetchImpl,
    serverIdFactory: overrides.serverIdFactory ?? (() => "srv-deterministic"),
    ...overrides,
  });
  return { adapter, calls, fetchImpl };
}

describe("WebAppChannelAdapter", () => {
  describe("constructor validation", () => {
    it("throws when pushEndpointUrl is empty", () => {
      expect(
        () =>
          new WebAppChannelAdapter({
            pushEndpointUrl: "",
            hmacKey: "k",
            gatewayToken: "g",
            botId: "b",
            userId: "u",
          }),
      ).toThrow(/pushEndpointUrl/);
    });

    it("throws when hmacKey is empty", () => {
      expect(
        () =>
          new WebAppChannelAdapter({
            pushEndpointUrl: "https://x",
            hmacKey: "",
            gatewayToken: "g",
            botId: "b",
            userId: "u",
          }),
      ).toThrow(/hmacKey/);
    });

    it("throws when gatewayToken is empty", () => {
      expect(
        () =>
          new WebAppChannelAdapter({
            pushEndpointUrl: "https://x",
            hmacKey: "k",
            gatewayToken: "",
            botId: "b",
            userId: "u",
          }),
      ).toThrow(/gatewayToken/);
    });

    it("throws when botId is empty", () => {
      expect(
        () =>
          new WebAppChannelAdapter({
            pushEndpointUrl: "https://x",
            hmacKey: "k",
            gatewayToken: "g",
            botId: "",
            userId: "u",
          }),
      ).toThrow(/botId/);
    });

    it("throws when userId is empty", () => {
      expect(
        () =>
          new WebAppChannelAdapter({
            pushEndpointUrl: "https://x",
            hmacKey: "k",
            gatewayToken: "g",
            botId: "b",
            userId: "",
          }),
      ).toThrow(/userId/);
    });
  });

  describe("send", () => {
    it("POSTs to the push endpoint with Bearer auth + HMAC signature", async () => {
      const { adapter, calls } = makeAdapter();
      await adapter.send({ chatId: "general", text: "hello from cron" });
      expect(calls).toHaveLength(1);
      const call = calls[0]!;
      expect(call.url).toBe("https://chat.magi.local/v1/bot-push/message");
      expect(call.init?.method).toBe("POST");
      const headers = call.init?.headers as Record<string, string>;
      expect(headers.Authorization).toBe("Bearer gw-token");
      expect(headers["X-Push-Signature"]).toMatch(/^[0-9a-f]{64}$/);
      expect(headers["Content-Type"]).toBe("application/json");
      const body = JSON.parse(call.init?.body as string) as {
        channel: string;
        userId: string;
        content: string;
        serverId: string;
      };
      expect(body).toEqual({
        channel: "general",
        userId: "did:privy:user",
        content: "hello from cron",
        serverId: "srv-deterministic",
      });
    });

    it("signs with HMAC-SHA256 over botId:channel:userId:serverId:content", async () => {
      const { adapter, calls } = makeAdapter();
      await adapter.send({ chatId: "news", text: "briefing ready" });
      const headers = calls[0]!.init?.headers as Record<string, string>;
      const expected = adapter.computeSignature({
        botId: "bot-1",
        channelId: "news",
        userId: "did:privy:user",
        serverId: "srv-deterministic",
        content: "briefing ready",
      });
      expect(headers["X-Push-Signature"]).toBe(expected);
    });

    it("throws on non-202 response with status + snippet", async () => {
      const { fetchImpl } = makeStubFetch(() => ({
        status: 401,
        body: JSON.stringify({ error: "invalid signature" }),
      }));
      const { adapter } = makeAdapter({ fetchImpl });
      await expect(
        adapter.send({ chatId: "general", text: "x" }),
      ).rejects.toThrow(/HTTP 401/);
    });

    it("accepts 200 as well as 202 (idempotent insert returns 202)", async () => {
      const { fetchImpl } = makeStubFetch(() => ({ status: 200, body: "" }));
      const { adapter } = makeAdapter({ fetchImpl });
      await expect(
        adapter.send({ chatId: "general", text: "x" }),
      ).resolves.toBeUndefined();
    });

    it("generates a unique serverId per call when factory not injected", async () => {
      // Without a serverIdFactory the default uses Date.now() + randomBytes.
      const { fetchImpl, calls } = makeStubFetch();
      const adapter = new WebAppChannelAdapter({
        pushEndpointUrl: "https://chat.magi.local/v1/bot-push/message",
        hmacKey: "k",
        gatewayToken: "g",
        botId: "b",
        userId: "u",
        fetchImpl,
      });
      await adapter.send({ chatId: "general", text: "a" });
      await adapter.send({ chatId: "general", text: "b" });
      expect(calls).toHaveLength(2);
      const id1 = JSON.parse(calls[0]!.init?.body as string).serverId;
      const id2 = JSON.parse(calls[1]!.init?.body as string).serverId;
      expect(id1).not.toBe(id2);
    });
  });

  describe("lifecycle", () => {
    it("start/stop toggle isStarted()", async () => {
      const { adapter } = makeAdapter();
      expect(adapter.isStarted()).toBe(false);
      await adapter.start();
      expect(adapter.isStarted()).toBe(true);
      await adapter.stop();
      expect(adapter.isStarted()).toBe(false);
    });

    it("onInboundMessage stores the handler but never fires it", async () => {
      const { adapter } = makeAdapter();
      expect(adapter.hasInboundHandler()).toBe(false);
      adapter.onInboundMessage(async () => {
        throw new Error("should never be called");
      });
      expect(adapter.hasInboundHandler()).toBe(true);
      await adapter.start();
      await adapter.stop();
      // No throw — the handler was never invoked by start/stop lifecycle.
    });
  });

  describe("file delivery (out of scope)", () => {
    it("sendDocument throws with a guidance message", async () => {
      const { adapter } = makeAdapter();
      await expect(
        adapter.sendDocument("general", "/tmp/x.pdf"),
      ).rejects.toThrow(/not supported/);
    });

    it("sendPhoto throws with a guidance message", async () => {
      const { adapter } = makeAdapter();
      await expect(adapter.sendPhoto("general", "/tmp/x.png")).rejects.toThrow(
        /not supported/,
      );
    });
  });

  describe("kind discriminator", () => {
    it("exposes kind='webapp'", () => {
      const { adapter } = makeAdapter();
      expect(adapter.kind).toBe("webapp");
    });
  });
});
