// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { deriveKey, encryptMessage } from "./e2ee";
import { encodeHistoryPlaintext } from "./history-envelope";
import { wrapPlaintext } from "./plaintext-sentinel";
import { rowToMessage, loadChannelHistory } from "./load-channel-history";
import type { E2EEApiMessage } from "./load-channel-history";

// Fixed hex signature for deterministic key generation in tests.
const SIGNATURE =
  "0x000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f";

function makeRow(overrides: Partial<E2EEApiMessage>): E2EEApiMessage {
  return {
    id: "srv-1",
    channel_name: "general",
    role: "assistant",
    encrypted_content: "",
    iv: "",
    created_at: new Date(1000).toISOString(),
    client_msg_id: null,
    ...overrides,
  };
}

describe("rowToMessage", () => {
  it("decodes a plaintext-sentinel row without any keys", async () => {
    const envelope = encodeHistoryPlaintext({
      role: "assistant",
      content: "hello world",
      thinkingContent: "thinking...",
      thinkingDuration: 3,
    });
    const row = makeRow({
      encrypted_content: wrapPlaintext(envelope),
      iv: "",
    });

    const msg = await rowToMessage(row, []);

    expect(msg).not.toBeNull();
    expect(msg!.content).toBe("hello world");
    expect(msg!.thinkingContent).toBe("thinking...");
    expect(msg!.thinkingDuration).toBe(3);
  });

  it("uses client_msg_id as id when present, falls back to row.id", async () => {
    const row = makeRow({
      encrypted_content: wrapPlaintext("hi"),
      iv: "",
      client_msg_id: "client-abc",
      id: "srv-xyz",
    });

    const msg = await rowToMessage(row, []);
    expect(msg!.id).toBe("client-abc");
    expect(msg!.serverId).toBe("srv-xyz");
  });

  it("uses row.id as id when client_msg_id is null", async () => {
    const row = makeRow({
      encrypted_content: wrapPlaintext("hi"),
      iv: "",
      client_msg_id: null,
      id: "srv-xyz",
    });

    const msg = await rowToMessage(row, []);
    expect(msg!.id).toBe("srv-xyz");
  });

  it("maps timestamp from created_at", async () => {
    const ts = new Date("2024-01-15T12:00:00.000Z").getTime();
    const row = makeRow({
      encrypted_content: wrapPlaintext("hi"),
      iv: "",
      created_at: new Date(ts).toISOString(),
    });

    const msg = await rowToMessage(row, []);
    expect(msg!.timestamp).toBe(ts);
  });

  it("decrypts a legacy encrypted row with a matching key", async () => {
    const key = await deriveKey(SIGNATURE, "user-1");
    const envelope = encodeHistoryPlaintext({
      role: "assistant",
      content: "encrypted content",
    });
    const { encrypted, iv } = await encryptMessage(key, envelope);

    const row = makeRow({ encrypted_content: encrypted, iv });

    const msg = await rowToMessage(row, [key]);
    expect(msg).not.toBeNull();
    expect(msg!.content).toBe("encrypted content");
  });

  it("tries multiple keys and succeeds with the correct one", async () => {
    const wrongKey = await deriveKey("0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "user-2");
    const rightKey = await deriveKey(SIGNATURE, "user-1");
    const envelope = encodeHistoryPlaintext({ role: "user", content: "secret" });
    const { encrypted, iv } = await encryptMessage(rightKey, envelope);

    const row = makeRow({ role: "user", encrypted_content: encrypted, iv });

    const msg = await rowToMessage(row, [wrongKey, rightKey]);
    expect(msg).not.toBeNull();
    expect(msg!.content).toBe("secret");
  });

  it("returns null when no key can decrypt the row", async () => {
    const wrongKey = await deriveKey("0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "user-2");
    const rightKey = await deriveKey(SIGNATURE, "user-1");
    const { encrypted, iv } = await encryptMessage(rightKey, "irrelevant");

    const row = makeRow({ encrypted_content: encrypted, iv });

    const result = await rowToMessage(row, [wrongKey]);
    expect(result).toBeNull();
  });

  it("returns null when key list is empty and row is encrypted", async () => {
    const key = await deriveKey(SIGNATURE, "user-1");
    const { encrypted, iv } = await encryptMessage(key, "secret");

    const row = makeRow({ encrypted_content: encrypted, iv });
    const result = await rowToMessage(row, []);
    expect(result).toBeNull();
  });
});

describe("loadChannelHistory", () => {
  async function buildMixedRows() {
    const key = await deriveKey(SIGNATURE, "user-1");

    // Row 1: plaintext sentinel (ts=3000)
    const ptEnvelope = encodeHistoryPlaintext({ role: "user", content: "plain user msg" });
    const ptRow: E2EEApiMessage = {
      id: "srv-pt",
      channel_name: "ch",
      role: "user",
      encrypted_content: wrapPlaintext(ptEnvelope),
      iv: "",
      created_at: new Date(3000).toISOString(),
      client_msg_id: "cid-pt",
    };

    // Row 2: legacy encrypted, decryptable (ts=1000)
    const encEnvelope = encodeHistoryPlaintext({ role: "assistant", content: "encrypted reply" });
    const { encrypted, iv } = await encryptMessage(key, encEnvelope);
    const encRow: E2EEApiMessage = {
      id: "srv-enc",
      channel_name: "ch",
      role: "assistant",
      encrypted_content: encrypted,
      iv,
      created_at: new Date(1000).toISOString(),
      client_msg_id: "cid-enc",
    };

    // Row 3: legacy encrypted, undecryptable (ts=2000)
    const otherKey = await deriveKey("0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "user-x");
    const { encrypted: badEnc, iv: badIv } = await encryptMessage(otherKey, "secret");
    const badRow: E2EEApiMessage = {
      id: "srv-bad",
      channel_name: "ch",
      role: "assistant",
      encrypted_content: badEnc,
      iv: badIv,
      created_at: new Date(2000).toISOString(),
      client_msg_id: null,
    };

    return { key, rows: [ptRow, encRow, badRow] };
  }

  it("returns decoded messages sorted by timestamp, counts decryptFailures", async () => {
    const { key, rows } = await buildMixedRows();

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        messages: rows,
        deletions: [{ client_msg_id: "del-1", deleted_at: "2024-01-01T00:00:00Z" }],
        hasMore: true,
        nextBefore: "cursor-abc",
      }),
    } as unknown as Response);

    const result = await loadChannelHistory({
      botId: "bot-1",
      channelName: "ch",
      keys: [key],
      token: "tok",
      fetchImpl: mockFetch,
    });

    // 2 decoded (plaintext + encrypted), 1 failed
    expect(result.messages).toHaveLength(2);
    expect(result.decryptFailures).toBe(1);

    // sorted ascending by timestamp: encRow(1000) < ptRow(3000)
    expect(result.messages[0].content).toBe("encrypted reply");
    expect(result.messages[1].content).toBe("plain user msg");

    // passthrough fields
    expect(result.hasMore).toBe(true);
    expect(result.nextBefore).toBe("cursor-abc");
    expect(result.deletions).toHaveLength(1);
    expect(result.deletions[0].client_msg_id).toBe("del-1");
  });

  it("builds the correct URL with query params", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ messages: [], deletions: [], hasMore: false, nextBefore: null }),
    } as unknown as Response);

    await loadChannelHistory({
      botId: "bot-42",
      channelName: "my-channel",
      keys: [],
      token: "tok",
      limit: 50,
      since: "ts-since",
      before: "cursor-before",
      latest: true,
      fetchImpl: mockFetch,
    });

    const calledUrl: string = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("botId=bot-42");
    expect(calledUrl).toContain("channelName=my-channel");
    expect(calledUrl).toContain("limit=50");
    expect(calledUrl).toContain("since=ts-since");
    expect(calledUrl).toContain("before=cursor-before");
    expect(calledUrl).toContain("latest=true");
  });

  it("sends the Bearer token in the Authorization header", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ messages: [], deletions: [], hasMore: false, nextBefore: null }),
    } as unknown as Response);

    await loadChannelHistory({
      botId: "b",
      channelName: "c",
      keys: [],
      token: "my-token",
      fetchImpl: mockFetch,
    });

    const calledInit = mockFetch.mock.calls[0][1] as RequestInit;
    expect((calledInit.headers as Record<string, string>)["Authorization"]).toBe("Bearer my-token");
  });

  it("returns empty result on non-ok HTTP response", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
    } as unknown as Response);

    const result = await loadChannelHistory({
      botId: "b",
      channelName: "c",
      keys: [],
      token: "bad",
      fetchImpl: mockFetch,
    });

    expect(result.messages).toHaveLength(0);
    expect(result.deletions).toHaveLength(0);
    expect(result.hasMore).toBe(false);
    expect(result.nextBefore).toBeNull();
    expect(result.decryptFailures).toBe(0);
  });

  it("returns empty result when fetchImpl rejects (network error)", async () => {
    const mockFetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));

    const result = await loadChannelHistory({
      botId: "b",
      channelName: "c",
      keys: [],
      token: "tok",
      fetchImpl: mockFetch,
    });

    expect(result.messages).toHaveLength(0);
    expect(result.deletions).toHaveLength(0);
    expect(result.hasMore).toBe(false);
    expect(result.nextBefore).toBeNull();
    expect(result.decryptFailures).toBe(0);
  });

  it("returns empty result when response body has messages: null", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ messages: null }),
    } as unknown as Response);

    const result = await loadChannelHistory({
      botId: "b",
      channelName: "c",
      keys: [],
      token: "tok",
      fetchImpl: mockFetch,
    });

    expect(result.messages).toHaveLength(0);
    expect(result.deletions).toHaveLength(0);
    expect(result.hasMore).toBe(false);
    expect(result.nextBefore).toBeNull();
    expect(result.decryptFailures).toBe(0);
  });

  it("decodes plaintext rows with no keys provided", async () => {
    const row: E2EEApiMessage = {
      id: "srv-1",
      channel_name: "ch",
      role: "user",
      encrypted_content: wrapPlaintext(encodeHistoryPlaintext({ role: "user", content: "plain text message" })),
      iv: "",
      created_at: new Date(5000).toISOString(),
      client_msg_id: "c1",
    };

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ messages: [row], deletions: [], hasMore: false, nextBefore: null }),
    } as unknown as Response);

    const result = await loadChannelHistory({
      botId: "b",
      channelName: "ch",
      keys: [],
      token: "tok",
      fetchImpl: mockFetch,
    });

    expect(result.messages).toHaveLength(1);
    expect(result.messages[0].content).toBe("plain text message");
    expect(result.decryptFailures).toBe(0);
  });

  it("filters out server-readable user-turn marker rows so they never render", async () => {
    // chat-proxy/core-agent-resume persists Python-ADK user turns as a
    // hidden HTML-comment marker. loadChannelHistory must drop these so
    // every caller (StreamChatContainer, legacy view-client, …) is
    // covered uniformly without per-call filtering.
    const markerContent =
      "<!-- openmagi:server-readable-user-turn:v1:eyJjb250ZW50IjoiaGVsbG8ifQ -->";
    const visibleContent = "real user message";

    const markerRow: E2EEApiMessage = {
      id: "srv-marker",
      channel_name: "ch",
      role: "user",
      encrypted_content: wrapPlaintext(
        encodeHistoryPlaintext({ role: "user", content: markerContent }),
      ),
      iv: "",
      created_at: new Date(1000).toISOString(),
      client_msg_id: "c-marker",
    };
    const visibleRow: E2EEApiMessage = {
      id: "srv-visible",
      channel_name: "ch",
      role: "user",
      encrypted_content: wrapPlaintext(
        encodeHistoryPlaintext({ role: "user", content: visibleContent }),
      ),
      iv: "",
      created_at: new Date(2000).toISOString(),
      client_msg_id: "c-visible",
    };

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        messages: [markerRow, visibleRow],
        deletions: [],
        hasMore: false,
        nextBefore: null,
      }),
    } as unknown as Response);

    const result = await loadChannelHistory({
      botId: "b",
      channelName: "ch",
      keys: [],
      token: "tok",
      fetchImpl: mockFetch,
    });

    expect(result.messages).toHaveLength(1);
    expect(result.messages[0].content).toBe(visibleContent);
    expect(result.decryptFailures).toBe(0);
  });

  it("tolerates trailing whitespace around the marker (regex is anchored with \\s*)", async () => {
    const markerRow: E2EEApiMessage = {
      id: "srv-marker-ws",
      channel_name: "ch",
      role: "user",
      encrypted_content: wrapPlaintext(
        encodeHistoryPlaintext({
          role: "user",
          content:
            "  <!-- openmagi:server-readable-user-turn:v1:eyJjb250ZW50IjoieCJ9 -->  \n",
        }),
      ),
      iv: "",
      created_at: new Date(3000).toISOString(),
      client_msg_id: "c-marker-ws",
    };

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        messages: [markerRow],
        deletions: [],
        hasMore: false,
        nextBefore: null,
      }),
    } as unknown as Response);

    const result = await loadChannelHistory({
      botId: "b",
      channelName: "ch",
      keys: [],
      token: "tok",
      fetchImpl: mockFetch,
    });

    expect(result.messages).toHaveLength(0);
  });
});
