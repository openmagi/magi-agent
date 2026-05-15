import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  createStreamingTextSmoother,
  injectMessage,
  buildSessionKey,
  interruptTurn,
  fetchControlEvents,
  fetchControlRequests,
  respondToControlRequest,
  fetchChannels,
  createChannel,
  deleteChannel,
  updateChannel,
  sendMessage,
  setChatTokenGetter,
} from "./chat-client";
import type {
  BrowserFrame,
  CitationGateStatus,
  ControlRequestRecord,
  DocumentDraftPreview,
  InspectedSource,
  RuntimeTrace,
  SubagentActivity,
  ToolActivity,
} from "./types";

/**
 * Tests for the mid-turn inject helper (#86). The helper is a thin
 * wrapper around `fetch` plus graceful fallback semantics — we mock
 * `fetch` + the token getter and verify the happy path, 409 fallback,
 * and the session-key builder.
 */

const originalFetch = globalThis.fetch;

function mockFetch(
  status: number,
  body: unknown,
): ReturnType<typeof vi.fn> {
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

function mockSseFetch(payload: string): ReturnType<typeof vi.fn> {
  const encoder = new TextEncoder();
  const impl = vi.fn(async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(payload));
        controller.close();
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  });
  globalThis.fetch = impl as unknown as typeof globalThis.fetch;
  return impl;
}

function mockOpenStreamingFetch(payload: string, snapshotContent: string): {
  fetchMock: ReturnType<typeof vi.fn>;
  closeStream: () => void;
} {
  const encoder = new TextEncoder();
  let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
  const impl = vi.fn(async (input: string | URL | Request) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    if (url.includes("/active-snapshot/")) {
      return new Response(JSON.stringify({
        snapshot: {
          turnId: "turn-1",
          status: "running",
          content: snapshotContent,
          thinking: "",
          startedAt: 1,
          updatedAt: 2,
        },
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
        controller.enqueue(encoder.encode(payload));
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  });
  globalThis.fetch = impl as unknown as typeof globalThis.fetch;

  return {
    fetchMock: impl,
    closeStream: () => {
      streamController?.enqueue(encoder.encode("data: [DONE]\n\n"));
      streamController?.close();
    },
  };
}

function errorMessage(error: Error | null): string | undefined {
  return error?.message;
}

beforeEach(() => {
  setChatTokenGetter(async () => "test-token");
});

afterEach(() => {
  vi.useRealTimers();
  globalThis.fetch = originalFetch;
});

describe("createStreamingTextSmoother", () => {
  it("paces oversized deltas instead of emitting them as one burst", async () => {
    vi.useFakeTimers();
    const received: string[] = [];
    const text = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    const smoother = createStreamingTextSmoother((delta) => received.push(delta), {
      burstThresholdChars: 24,
      initialChars: 16,
      charsPerTick: 8,
      tickMs: 20,
    });

    smoother.push(text);

    expect(received).toEqual([text.slice(0, 16)]);

    const flush = smoother.flush();
    await vi.runAllTimersAsync();
    await flush;

    expect(received.join("")).toBe(text);
    expect(received.length).toBeGreaterThan(1);
    expect(received.slice(1).every((delta) => delta.length <= 8)).toBe(true);
  });

  it("emits first chunk synchronously after clear so clear+push is atomic", () => {
    const received: string[] = [];
    const smoother = createStreamingTextSmoother((delta) => received.push(delta), {
      burstThresholdChars: 24,
      initialChars: 16,
      charsPerTick: 8,
      tickMs: 20,
    });

    smoother.push("legacy content that is long enough");
    const beforeClear = [...received];
    expect(beforeClear.length).toBeGreaterThan(0);

    received.length = 0;
    smoother.clear();
    smoother.push("new agent content");

    // The first chunk after clear must be emitted synchronously (no timer gap)
    // so that a UI state reset + new content happen in the same JS tick.
    expect(received.length).toBeGreaterThan(0);
    expect(received[0]).toBe("new agent content".slice(0, 16));
  });

  it("paces small deltas into a fast typing cadence", async () => {
    vi.useFakeTimers();
    const received: string[] = [];
    const smoother = createStreamingTextSmoother((delta) => received.push(delta), {
      burstThresholdChars: 24,
      initialChars: 1,
      charsPerTick: 1,
      tickMs: 10,
    });

    smoother.push("hello ");
    smoother.push("world");

    expect(received).toEqual(["h"]);
    expect(vi.getTimerCount()).toBe(1);

    const flush = smoother.flush();
    await vi.runAllTimersAsync();
    await flush;

    expect(received.join("")).toBe("hello world");
    expect(received.slice(1).every((delta) => delta.length === 1)).toBe(true);
  });
});

describe("injectMessage", () => {
  it("returns { injected: true, injectionId } on 200", async () => {
    const fetchMock = mockFetch(200, { injectionId: "inj-xyz", queuedCount: 1 });
    const r = await injectMessage(
      "bot-abc",
      "agent:main:app:general",
      "hello mid-turn",
      "web",
    );
    expect(r.injected).toBe(true);
    expect(r.injectionId).toBe("inj-xyz");
    expect(r.status).toBe(200);

    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/chat/bot-abc/inject");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body as string);
    expect(body).toEqual({
      sessionKey: "agent:main:app:general",
      text: "hello mid-turn",
      source: "web",
    });
    const headers = opts.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer test-token");
  });

  it("returns { injected: false, reason: 'no_active_turn' } on 409 (fallback signal)", async () => {
    mockFetch(409, { error: "no_active_turn", hint: "send to /turn instead" });
    const r = await injectMessage(
      "bot-abc",
      "agent:main:app:general",
      "hello",
    );
    expect(r.injected).toBe(false);
    expect(r.status).toBe(409);
    expect(r.reason).toBe("no_active_turn");
  });

  it("returns { injected: false } on 404 (session expired)", async () => {
    mockFetch(404, { error: "no_session" });
    const r = await injectMessage(
      "bot-abc",
      "agent:main:app:general",
      "hello",
    );
    expect(r.injected).toBe(false);
    expect(r.status).toBe(404);
    expect(r.reason).toBe("no_session");
  });

  it("returns { injected: false } on 429 (rate limited)", async () => {
    mockFetch(429, { error: "too_many_injections" });
    const r = await injectMessage(
      "bot-abc",
      "agent:main:app:general",
      "hello",
    );
    expect(r.injected).toBe(false);
    expect(r.status).toBe(429);
  });

  it("returns { injected: false, status: 0 } on network failure", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network offline");
    }) as unknown as typeof globalThis.fetch;
    const r = await injectMessage(
      "bot-abc",
      "agent:main:app:general",
      "hello",
    );
    expect(r.injected).toBe(false);
    expect(r.status).toBe(0);
    expect(r.reason).toContain("network");
  });

  it("defaults source to 'web' when omitted", async () => {
    const fetchMock = mockFetch(200, { injectionId: "i1" });
    await injectMessage("bot-abc", "agent:main:app:general", "hi");
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(opts.body as string);
    expect(body.source).toBe("web");
  });

  it("forwards source='mobile' when passed", async () => {
    const fetchMock = mockFetch(200, { injectionId: "i1" });
    await injectMessage("bot-abc", "agent:main:app:general", "hi", "mobile");
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(opts.body as string);
    expect(body.source).toBe("mobile");
  });
});

describe("updateChannel", () => {
  it("sends channel model preferences as persistence-only metadata", async () => {
    const fetchMock = mockFetch(200, { channel: { name: "general" } });

    await updateChannel("bot-abc", "general", {
      model_selection: "kimi_k2_5",
      router_type: "standard",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/chat/bot-abc/channels/general"),
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          model_selection: "kimi_k2_5",
          router_type: "standard",
        }),
      }),
    );
  });
});

describe("buildSessionKey", () => {
  // Clear localStorage-backed reset counters between runs.
  beforeEach(() => {
    if (typeof localStorage !== "undefined") {
      localStorage.clear();
    }
  });

  it("uses the base form when no reset counter is set", () => {
    const key = buildSessionKey("bot-abc", "general");
    expect(key).toBe("agent:main:app:general");
  });
});

describe("fetchChannels", () => {
  it("falls back to the legacy chat proxy when the Open Magi proxy is unreachable", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({
          channels: [
            {
              id: "ch-1",
              name: "general",
              display_name: "General",
              position: 0,
              category: "General",
              created_at: "2026-05-06T00:00:00.000Z",
            },
          ],
        }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    const channels = await fetchChannels("bot-abc");

    expect(channels).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("https://chat.openmagi.ai/v1/chat/bot-abc/channels");
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe("https://chat.clawy.pro/v1/chat/bot-abc/channels");
  });
});

describe("deleteChannel", () => {
  it("throws when the chat proxy rejects the delete request", async () => {
    const fetchMock = mockFetch(500, { error: "Failed to delete channel" });

    await expect(deleteChannel("bot-abc", "fig-app-deal")).rejects.toThrow("Failed to delete channel");

    expect(fetchMock).toHaveBeenCalledWith(
      "https://chat.openmagi.ai/v1/chat/bot-abc/channels/fig-app-deal",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

describe("createChannel", () => {
  it("sends incognito memory mode when requested", async () => {
    const fetchMock = mockFetch(201, {
      channel: {
        id: "ch-1",
        name: "private",
        display_name: "Private",
        position: 1,
        category: "General",
        memory_mode: "incognito",
        created_at: "2026-05-08T00:00:00.000Z",
      },
    });

    const channel = await createChannel(
      "bot-abc",
      "private",
      "Private",
      undefined,
      "incognito",
    );

    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(opts.body as string)).toMatchObject({
      name: "private",
      displayName: "Private",
      memoryMode: "incognito",
    });
    expect(channel.memory_mode).toBe("incognito");
  });

  it("sends read-only memory mode when requested", async () => {
    const fetchMock = mockFetch(201, {
      channel: {
        id: "ch-1",
        name: "research",
        display_name: "Research",
        position: 1,
        category: "General",
        memory_mode: "read_only",
        created_at: "2026-05-08T00:00:00.000Z",
      },
    });

    const channel = await createChannel(
      "bot-abc",
      "research",
      "Research",
      undefined,
      "read_only",
    );

    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(opts.body as string)).toMatchObject({
      name: "research",
      displayName: "Research",
      memoryMode: "read_only",
    });
    expect(channel.memory_mode).toBe("read_only");
  });
});

describe("interruptTurn", () => {
  it("returns { accepted: true } on 200", async () => {
    const fetchMock = mockFetch(200, { status: "accepted", handoffRequested: true });
    const r = await interruptTurn(
      "bot-abc",
      "agent:main:app:general",
      true,
      "web",
    );
    expect(r.accepted).toBe(true);
    expect(r.handoffRequested).toBe(true);
    expect(r.status).toBe(200);

    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/chat/bot-abc/interrupt");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body as string);
    expect(body).toEqual({
      sessionKey: "agent:main:app:general",
      handoffRequested: true,
      source: "web",
    });
  });

  it("returns { accepted: false, reason: 'no_active_turn' } on 409", async () => {
    mockFetch(409, { error: "no_active_turn" });
    const r = await interruptTurn("bot-abc", "agent:main:app:general", true);
    expect(r.accepted).toBe(false);
    expect(r.reason).toBe("no_active_turn");
    expect(r.status).toBe(409);
  });
});

describe("sendMessage SSE agent events", () => {
  it("includes goalMode in the streaming request body when enabled", async () => {
    const fetchSpy = mockSseFetch(
      [
        "event: agent",
        'data: {"type":"text_delta","delta":"ok"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    await sendMessage("bot-abc", "general", [{ role: "user", content: "finish this" }], {
      goalMode: true,
      onDelta: () => {},
      onDone: () => {},
      onError: () => {},
    });

    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init).toBeDefined();
    expect(JSON.parse(String(init?.body))).toMatchObject({
      goalMode: true,
    });
  });

  it("hides internal verifier failure details from user-visible stream errors", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"error","code":"turn_failed","message":"beforeCommit blocked: hook:builtin:self-claim-verifier threw: Error: hook timeout: builtin:self-claim-verifier (5000ms)"}',
        "",
        "event: agent",
        'data: {"type":"turn_end","status":"aborted","reason":"beforeCommit blocked: hook:builtin:self-claim-verifier threw: Error: hook timeout: builtin:self-claim-verifier (5000ms)"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    let error: Error | null = null;

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onDone: () => {},
      onError: (err) => {
        error = err;
      },
    });

    const message = errorMessage(error);
    expect(message).toBe("응답 검증 중 내부 오류가 발생했습니다. 다시 시도해 주세요.");
    expect(message).not.toContain("beforeCommit");
    expect(message).not.toContain("hook:");
    expect(message).not.toContain("self-claim-verifier");
  });

  it("hides raw overloaded provider JSON from agent stream errors", async () => {
    const rawProviderError =
      'API Error: {"type":"error","error":{"details":null,"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CamZqhiTyPr2jzbjM7P9b"}';
    mockSseFetch(
      [
        "event: agent",
        `data: ${JSON.stringify({ type: "error", code: "turn_failed", message: rawProviderError })}`,
        "",
        "event: agent",
        `data: ${JSON.stringify({ type: "turn_end", status: "aborted", reason: rawProviderError })}`,
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    let error: Error | null = null;

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onDone: () => {},
      onError: (err) => {
        error = err;
      },
    });

    const message = errorMessage(error);
    expect(message).toBe("The model is temporarily busy. Please try again in a moment.");
    expect(message).not.toContain("overloaded_error");
    expect(message).not.toContain("request_id");
    expect(message).not.toContain("{");
  });

  it("hides raw overloaded provider JSON from failed HTTP stream responses", async () => {
    mockFetch(
      529,
      'API Error: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_http"}',
    );

    let error: Error | null = null;

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onDone: () => {},
      onError: (err) => {
        error = err;
      },
    });

    const message = errorMessage(error);
    expect(message).toBe("The model is temporarily busy. Please try again in a moment.");
    expect(message).not.toContain("req_http");
    expect(message).not.toContain("overloaded_error");
  });

  it("does not stream raw overloaded provider JSON when it arrives as text", async () => {
    const rawProviderError =
      'API Error: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_text"}';
    mockSseFetch(
      [
        "event: agent",
        `data: ${JSON.stringify({ type: "text_delta", delta: rawProviderError })}`,
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const deltas: string[] = [];
    let error: Error | null = null;

    await sendMessage("bot-abc", "general", [], {
      onDelta: (delta) => {
        deltas.push(delta);
      },
      onDone: () => {},
      onError: (err) => {
        error = err;
      },
    });

    expect(deltas).toEqual([]);
    const message = errorMessage(error);
    expect(message).toBe("The model is temporarily busy. Please try again in a moment.");
    expect(message).not.toContain("req_text");
  });

  it("suppresses verifier meta text and terminal errors after visible content", async () => {
    mockSseFetch(
      [
        "event: agent",
        `data: ${JSON.stringify({
          type: "text_delta",
          delta: "브라우저 기본 기능 테스트 완료했습니다.",
        })}`,
        "",
        "event: agent",
        `data: ${JSON.stringify({
          type: "error",
          code: "turn_failed",
          message:
            "I could not complete a source-verified final answer for this request. Please retry with a narrower scope or ask me to continue from the inspected-source context.",
        })}`,
        "",
        "event: agent",
        `data: ${JSON.stringify({
          type: "text_delta",
          delta:
            "I could not complete a source-verified final answer for this request. Please retry with a narrower scope or ask me to continue from the inspected-source context.",
        })}`,
        "",
        "event: agent",
        `data: ${JSON.stringify({
          type: "turn_end",
          turnId: "turn-1",
          status: "aborted",
          stopReason: "aborted",
          reason:
            "The runtime verifier stopped this run because the assistant promised work without completing it.",
        })}`,
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const deltas: string[] = [];
    let done = false;
    let error: Error | null = null;

    await sendMessage("bot-abc", "general", [], {
      onDelta: (delta) => {
        deltas.push(delta);
      },
      onDone: () => {
        done = true;
      },
      onError: (err) => {
        error = err;
      },
    });

    expect(deltas.join("")).toBe("브라우저 기본 기능 테스트 완료했습니다.");
    expect(done).toBe(true);
    expect(error).toBeNull();
  });

  it("emits final turn usage from committed agent turn_end events", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "event: agent",
        'data: {"type":"turn_end","status":"committed","usage":{"inputTokens":1234,"outputTokens":56,"costUsd":0.0123}}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const usageSnapshots: unknown[] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onUsage: (usage) => {
        usageSnapshots.push(usage);
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(usageSnapshots).toEqual([
      {
        inputTokens: 1234,
        outputTokens: 56,
        costUsd: 0.0123,
      },
    ]);
  });

  it("paces oversized SSE content before completing the stream", async () => {
    vi.useFakeTimers();
    const text = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".repeat(2);
    mockSseFetch(
      [
        `data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}`,
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const received: string[] = [];
    let done = false;
    const promise = sendMessage("bot-abc", "general", [], {
      onDelta: (delta) => received.push(delta),
      onDone: () => {
        done = true;
      },
      onError: (error) => {
        throw error;
      },
    });

    for (let i = 0; i < 5 && received.length === 0; i += 1) {
      await Promise.resolve();
    }

    expect(received.length).toBeGreaterThan(0);
    expect(received.join("").length).toBeLessThan(text.length);
    expect(done).toBe(false);

    await vi.runAllTimersAsync();
    await promise;

    expect(received.join("")).toBe(text);
    expect(received.length).toBeGreaterThan(1);
    expect(done).toBe(true);
  });

  it("clears early legacy deltas when the agent text channel takes over", async () => {
    mockSseFetch(
      [
        'data: {"choices":[{"delta":{"content":"draft "}}]}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"draft "}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"final."}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    let text = "";
    let clears = 0;

    await sendMessage("bot-abc", "general", [], {
      onDelta: (delta) => {
        text += delta;
      },
      onResponseClear: () => {
        clears += 1;
        text = "";
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(clears).toBe(1);
    expect(text).toBe("draft final.");
  });

  it("uses onContentReplace for legacy→agent transition to avoid flicker", async () => {
    mockSseFetch(
      [
        'data: {"choices":[{"delta":{"content":"draft "}}]}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"draft "}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"final."}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    let text = "";
    let clears = 0;
    const replaces: string[] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: (delta) => {
        text += delta;
      },
      onContentReplace: (content) => {
        replaces.push(content);
        text = content;
      },
      onResponseClear: () => {
        clears += 1;
        text = "";
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    // onContentReplace should be called instead of onResponseClear
    expect(clears).toBe(0);
    expect(replaces).toEqual(["draft "]);
    expect(text).toBe("draft final.");
  });

  it("treats response_clear as removing previously received content", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"text_delta","delta":"old answer"}',
        "",
        "event: agent",
        'data: {"type":"response_clear"}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"new answer"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    let text = "";

    await sendMessage("bot-abc", "general", [], {
      onDelta: (delta) => {
        text += delta;
      },
      onResponseClear: () => {
        text = "";
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(text).toBe("new answer");
  });

  it("repairs replacement-character live deltas from the active snapshot while the stream remains open", async () => {
    vi.useFakeTimers();
    const corruptedText = "두 리\uFFFD\uFFFD\uFFFD트 비교";
    const cleanText = "두 리포트 비교";
    const { fetchMock, closeStream } = mockOpenStreamingFetch(
      [
        "event: agent",
        `data: ${JSON.stringify({ type: "text_delta", delta: corruptedText })}`,
        "",
      ].join("\n"),
      cleanText,
    );

    let rendered = "";
    let clears = 0;
    const promise = sendMessage("bot-abc", "general", [], {
      onDelta: (delta) => {
        rendered += delta;
      },
      onResponseClear: () => {
        clears += 1;
        rendered = "";
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    for (let i = 0; i < 5; i += 1) {
      await Promise.resolve();
    }
    await vi.advanceTimersByTimeAsync(1_300);
    for (let i = 0; i < 5; i += 1) {
      await Promise.resolve();
    }
    closeStream();
    await vi.advanceTimersByTimeAsync(200);
    await promise;

    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/active-snapshot/"))).toBe(true);
    expect(clears).toBe(1);
    expect(rendered).toBe(cleanText);
    expect(rendered).not.toContain("\uFFFD");
  });

  it("uses onContentReplace for snapshot repair to avoid flicker", async () => {
    vi.useFakeTimers();
    const corruptedText = "두 리\uFFFD\uFFFD\uFFFD트 비교";
    const cleanText = "두 리포트 비교";
    const { fetchMock, closeStream } = mockOpenStreamingFetch(
      [
        "event: agent",
        `data: ${JSON.stringify({ type: "text_delta", delta: corruptedText })}`,
        "",
      ].join("\n"),
      cleanText,
    );

    let rendered = "";
    let clears = 0;
    const replaces: string[] = [];
    const promise = sendMessage("bot-abc", "general", [], {
      onDelta: (delta) => {
        rendered += delta;
      },
      onContentReplace: (text) => {
        replaces.push(text);
        rendered = text;
      },
      onResponseClear: () => {
        clears += 1;
        rendered = "";
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    for (let i = 0; i < 5; i += 1) {
      await Promise.resolve();
    }
    await vi.advanceTimersByTimeAsync(1_300);
    for (let i = 0; i < 5; i += 1) {
      await Promise.resolve();
    }
    closeStream();
    await vi.advanceTimersByTimeAsync(200);
    await promise;

    expect(clears).toBe(0);
    expect(replaces).toEqual([cleanText]);
    expect(rendered).toBe(cleanText);
  });

  it("decodes structured live activity events from the agent channel", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"turn_phase","phase":"planning"}',
        "",
        "event: agent",
        'data: {"type":"heartbeat","elapsedMs":12000}',
        "",
        "event: agent",
        'data: {"type":"injection_queued","queuedCount":2}',
        "",
        "event: agent",
        'data: {"type":"injection_drained","count":2,"iteration":1}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"hello"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const phases: string[] = [];
    const heartbeats: number[] = [];
    const queuedCounts: number[] = [];
    const deltas: string[] = [];
    let done = 0;

    await sendMessage("bot-abc", "general", [], {
      onDelta: (text) => deltas.push(text),
      onTurnPhase: (phase) => phases.push(phase),
      onHeartbeat: (elapsedMs) => heartbeats.push(elapsedMs),
      onPendingInjectionCount: (queuedCount) => queuedCounts.push(queuedCount),
      onDone: () => {
        done += 1;
      },
      onError: (error) => {
        throw error;
      },
    });

    expect(phases).toEqual(["planning"]);
    expect(heartbeats).toEqual([12_000]);
    expect(queuedCounts).toEqual([2, 0]);
    expect(deltas.join("")).toBe("hello");
    expect(deltas.length).toBeGreaterThan(1);
    expect(done).toBe(1);
  });

  it("surfaces model progress and heartbeat as live work activity", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"llm_progress","turnId":"turn-1","iter":0,"stage":"started","label":"Thinking through next step","detail":"Reading context"}',
        "",
        "event: agent",
        'data: {"type":"heartbeat","turnId":"turn-1","iter":0,"elapsedMs":30000,"lastEventAt":1}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: ToolActivity[][] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onToolActivity: (activities) => {
        snapshots.push(activities.map((activity) => ({ ...activity })));
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(
      snapshots.some((snapshot) =>
        snapshot.some((activity) =>
          activity.id === "llm:turn-1:0" &&
          activity.label === "ModelProgress" &&
          activity.status === "running" &&
          activity.inputPreview?.includes("Reading context") === true,
        ),
      ),
    ).toBe(true);
    expect(
      snapshots.some((snapshot) =>
        snapshot.some((activity) =>
          activity.id === "llm:turn-1:0" &&
          activity.outputPreview?.includes("30s") === true,
        ),
      ),
    ).toBe(true);
  });

  it("appends model heartbeat progress as public transcript entries", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"llm_progress","turnId":"turn-1","iter":0,"stage":"started","label":"Thinking through next step","detail":"Reading context"}',
        "",
        "event: agent",
        'data: {"type":"heartbeat","turnId":"turn-1","iter":0,"elapsedMs":30000,"lastEventAt":1}',
        "",
        "event: agent",
        'data: {"type":"heartbeat","turnId":"turn-1","iter":0,"elapsedMs":40000,"lastEventAt":1}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: ToolActivity[][] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onToolActivity: (activities) => {
        snapshots.push(activities.map((activity) => ({ ...activity })));
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    const latest = snapshots.at(-1) ?? [];
    expect(latest.map((activity) => activity.id)).toEqual(
      expect.arrayContaining([
        "llm:turn-1:0",
        "llm:turn-1:0:heartbeat:30",
        "llm:turn-1:0:heartbeat:40",
      ]),
    );
    expect(latest.find((activity) => activity.id === "llm:turn-1:0")?.status).toBe("done");
    expect(latest.find((activity) => activity.id === "llm:turn-1:0:heartbeat:30")?.status).toBe("done");
    expect(latest.find((activity) => activity.id === "llm:turn-1:0:heartbeat:40")?.outputPreview).toContain("40s");
  });

  it("uses public task-stage labels for model heartbeat transcript entries", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"llm_progress","turnId":"turn-1","iter":0,"stage":"started","label":"Thinking through next step","detail":"Reading context"}',
        "",
        "event: agent",
        'data: {"type":"heartbeat","turnId":"turn-1","iter":0,"elapsedMs":30000,"lastEventAt":1}',
        "",
        "event: agent",
        'data: {"type":"heartbeat","turnId":"turn-1","iter":0,"elapsedMs":40000,"lastEventAt":1}',
        "",
        "event: agent",
        'data: {"type":"heartbeat","turnId":"turn-1","iter":0,"elapsedMs":50000,"lastEventAt":1}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: ToolActivity[][] = [];

    await sendMessage(
      "bot-abc",
      "general",
      [
        {
          role: "user",
          content: "이 자료를 기반으로 보고서를 작성해줘.",
        },
      ],
      {
        onDelta: () => {},
        onToolActivity: (activities) => {
          snapshots.push(activities.map((activity) => ({ ...activity })));
        },
        onDone: () => {},
        onError: (error) => {
          throw error;
        },
      },
    );

    const latest = snapshots.at(-1) ?? [];
    expect(latest.find((activity) => activity.id === "llm:turn-1:0:heartbeat:30")?.inputPreview).toContain("요청 처리 중");
    expect(latest.find((activity) => activity.id === "llm:turn-1:0:heartbeat:40")?.inputPreview).toContain("다음 단계 준비 중");
    expect(latest.find((activity) => activity.id === "llm:turn-1:0:heartbeat:50")?.inputPreview).toContain("응답 구조 잡는 중");
  });

  it("appends public heartbeat transcript entries while a real tool stays running", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"tool_start","id":"tu_1","name":"FileRead","input_preview":"{\\"path\\":\\"workspace/stock-framework-2026-05/CONTEXT.md\\"}"}',
        "",
        "event: agent",
        'data: {"type":"heartbeat","elapsedMs":30000,"lastEventAt":1}',
        "",
        "event: agent",
        'data: {"type":"heartbeat","elapsedMs":40000,"lastEventAt":1}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: ToolActivity[][] = [];

    await sendMessage(
      "bot-abc",
      "general",
      [
        {
          role: "user",
          content: "자료를 읽고 주식 리서치 최종 보고서를 정리해줘.",
        },
      ],
      {
        onDelta: () => {},
        onToolActivity: (activities) => {
          snapshots.push(activities.map((activity) => ({ ...activity })));
        },
        onDone: () => {},
        onError: (error) => {
          throw error;
        },
      },
    );

    const latest = snapshots.at(-1) ?? [];
    expect(latest.map((activity) => activity.id)).toEqual(
      expect.arrayContaining([
        "tu_1",
        "tu_1:heartbeat:30",
        "tu_1:heartbeat:40",
      ]),
    );
    expect(latest.find((activity) => activity.id === "tu_1")?.label).toBe("FileRead");
    expect(latest.find((activity) => activity.id === "tu_1:heartbeat:30")?.label).toBe("ActivityProgress");
    expect(latest.find((activity) => activity.id === "tu_1:heartbeat:30")?.inputPreview).toContain("자료 읽는 중");
    expect(latest.find((activity) => activity.id === "tu_1:heartbeat:30")?.inputPreview).toContain("workspace/stock-framework-2026-05/CONTEXT.md");
    expect(latest.find((activity) => activity.id === "tu_1:heartbeat:40")?.outputPreview).toContain("40s");
  });

  it("appends heartbeat transcript entries for each running real tool", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"tool_start","id":"tu_read","name":"FileRead","input_preview":"{\\"path\\":\\"workspace/stock-framework-2026-05/CONTEXT.md\\"}"}',
        "",
        "event: agent",
        'data: {"type":"tool_start","id":"tu_time","name":"Time","input_preview":"{\\"target\\":\\"UTC\\"}"}',
        "",
        "event: agent",
        'data: {"type":"heartbeat","elapsedMs":30000,"lastEventAt":1}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: ToolActivity[][] = [];

    await sendMessage(
      "bot-abc",
      "general",
      [
        {
          role: "user",
          content: "자료를 읽고 현재 시간도 확인해서 최종 보고서를 정리해줘.",
        },
      ],
      {
        onDelta: () => {},
        onToolActivity: (activities) => {
          snapshots.push(activities.map((activity) => ({ ...activity })));
        },
        onDone: () => {},
        onError: (error) => {
          throw error;
        },
      },
    );

    const latest = snapshots.at(-1) ?? [];
    expect(latest.map((activity) => activity.id)).toEqual(
      expect.arrayContaining([
        "tu_read:heartbeat:30",
        "tu_time:heartbeat:30",
      ]),
    );
    expect(latest.find((activity) => activity.id === "tu_read:heartbeat:30")?.inputPreview)
      .toContain("자료 읽는 중");
    expect(latest.find((activity) => activity.id === "tu_time:heartbeat:30")?.inputPreview)
      .toContain("현재 시간 확인 중");
  });

  it("decorates the running tool activity when a structured retry event arrives", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"tool_start","id":"tu_1","name":"FileRead"}',
        "",
        "event: agent",
        'data: {"type":"retry","toolUseId":"tu_1","toolName":"FileRead","retryNo":1,"reason":"ETIMEDOUT"}',
        "",
        "event: agent",
        'data: {"type":"tool_end","id":"tu_1","status":"ok","durationMs":20,"output_preview":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: Array<Array<{ id: string; outputPreview?: string; status: string }>> = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onToolActivity: (activities) => {
        snapshots.push(activities.map((activity) => ({
          id: activity.id,
          outputPreview: activity.outputPreview,
          status: activity.status,
        })));
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(snapshots.some((snapshot) =>
      snapshot.some((activity) =>
        activity.id === "tu_1" &&
        activity.status === "running" &&
        activity.outputPreview?.includes("Retry 1") === true,
      ),
    )).toBe(true);
    expect(snapshots.at(-1)?.[0]).toMatchObject({
      id: "tu_1",
      status: "done",
      outputPreview: "done",
    });
  });

  it("surfaces browser preview frames from parent or child tool events", async () => {
    const imageBase64 = btoa("browser-frame");
    mockSseFetch(
      [
        "event: agent",
        `data: ${JSON.stringify({
          type: "browser_frame",
          action: "click",
          url: "https://example.com/app",
          imageBase64,
          contentType: "image/png",
          capturedAt: 123,
          cdpEndpoint: "ws://secret",
        })}`,
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const frames: BrowserFrame[] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onBrowserFrame: (frame) => frames.push(frame),
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(frames).toEqual([
      {
        action: "click",
        url: "https://example.com/app",
        imageBase64,
        contentType: "image/png",
        capturedAt: 123,
      },
    ]);
    expect(JSON.stringify(frames)).not.toContain("secret");
  });

  it("surfaces live document draft previews and marks them done on matching tool_end", async () => {
    mockSseFetch(
      [
        "event: agent",
        `data: ${JSON.stringify({
          type: "document_draft",
          id: "tu_doc",
          filename: "docs/report.md",
          format: "md",
          contentPreview: "# Draft",
          contentLength: 7,
          truncated: false,
        })}`,
        "",
        "event: agent",
        `data: ${JSON.stringify({
          type: "document_draft",
          id: "tu_doc",
          filename: "docs/report.md",
          format: "md",
          contentPreview: "# Draft\nBody",
          contentLength: 12,
          truncated: false,
        })}`,
        "",
        "event: agent",
        'data: {"type":"tool_end","id":"tu_doc","status":"ok","durationMs":20}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const drafts: Array<DocumentDraftPreview | null> = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onDocumentDraft: (draft) => drafts.push(draft),
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(drafts).toHaveLength(3);
    expect(drafts[0]).toMatchObject({
      id: "tu_doc",
      filename: "docs/report.md",
      format: "md",
      status: "streaming",
      contentPreview: "# Draft",
      contentLength: 7,
    });
    expect(drafts[1]).toMatchObject({
      status: "streaming",
      contentPreview: "# Draft\nBody",
      contentLength: 12,
    });
    expect(drafts[2]).toMatchObject({
      id: "tu_doc",
      status: "done",
      contentPreview: "# Draft\nBody",
    });
  });

  it("surfaces inspected sources and claim citation gate status from agent events", async () => {
    mockSseFetch([
      "event: agent",
      `data: ${JSON.stringify({
        type: "source_inspected",
        source: {
          sourceId: "src_1",
          kind: "web_fetch",
          uri: "https://example.com/report",
          title: "Example Report",
          inspectedAt: 123,
          toolName: "WebFetch",
          snippets: ["revenue increased"],
        },
      })}`,
      "",
      "event: agent",
      `data: ${JSON.stringify({
        type: "rule_check",
        ruleId: "claim-citation-gate",
        verdict: "violation",
        detail: "2 uncited claims",
      })}`,
      "",
      "data: [DONE]",
      "",
    ].join("\n"));

    const sources: InspectedSource[] = [];
    const citationStatuses: CitationGateStatus[] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onSourceInspected: (source) => sources.push(source),
      onCitationGate: (status) => citationStatuses.push(status),
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(sources).toEqual([{
      sourceId: "src_1",
      kind: "web_fetch",
      uri: "https://example.com/report",
      title: "Example Report",
      inspectedAt: 123,
      toolName: "WebFetch",
      snippets: ["revenue increased"],
    }]);
    expect(citationStatuses).toEqual([{
      ruleId: "claim-citation-gate",
      verdict: "violation",
      detail: "2 uncited claims",
      checkedAt: expect.any(Number),
    }]);
  });

  it("surfaces runtime contract traces from agent events and control replay", async () => {
    mockSseFetch([
      "event: agent",
      `data: ${JSON.stringify({
        type: "runtime_trace",
        turnId: "turn-1",
        phase: "verifier_blocked",
        severity: "warning",
        title: "Runtime verifier blocked completion",
        detail: "The draft promised work without tool evidence.",
        reasonCode: "GOAL_PROGRESS_EXECUTE_NEXT",
        attempt: 1,
        maxAttempts: 3,
        retryable: true,
      })}`,
      "",
      "event: agent",
      `data: ${JSON.stringify({
        type: "control_event",
        seq: 2,
        event: {
          type: "runtime_trace",
          turnId: "turn-1",
          phase: "retry_scheduled",
          severity: "info",
          title: "Retrying after runtime verifier block",
        },
      })}`,
      "",
      "data: [DONE]",
      "",
    ].join("\n"));

    const traces: RuntimeTrace[] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onRuntimeTrace: (trace) => traces.push(trace),
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(traces).toEqual([
      expect.objectContaining({
        turnId: "turn-1",
        phase: "verifier_blocked",
        severity: "warning",
        reasonCode: "GOAL_PROGRESS_EXECUTE_NEXT",
        attempt: 1,
        maxAttempts: 3,
        retryable: true,
      }),
      expect.objectContaining({
        turnId: "turn-1",
        phase: "retry_scheduled",
        severity: "info",
      }),
    ]);
  });

  it("surfaces child-agent lifecycle events as live activities", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"child_started","taskId":"blue"}',
        "",
        "event: agent",
        'data: {"type":"child_progress","taskId":"blue","detail":"Searching sources"}',
        "",
        "event: agent",
        'data: {"type":"child_completed","taskId":"blue"}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: Array<Array<{ id: string; outputPreview?: string; status: string }>> = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onToolActivity: (activities) => {
        snapshots.push(activities.map((activity) => ({
          id: activity.id,
          outputPreview: activity.outputPreview,
          status: activity.status,
        })));
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(snapshots.some((snapshot) =>
      snapshot.some((activity) =>
        activity.id === "child:blue" &&
        activity.status === "running" &&
        activity.outputPreview?.includes("Searching sources") === true,
      ),
    )).toBe(true);
    expect(snapshots.at(-1)?.[0]).toMatchObject({
      id: "child:blue",
      status: "done",
    });
  });

  it("accumulates streamed tool-call arguments so helper assignments have concrete previews", async () => {
    mockSseFetch(
      [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_spawn","function":{"name":"SpawnAgent","arguments":"{\\"persona\\":\\"skeptic-partner\\","}}]}}]}',
        "",
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"prompt\\":\\"Task: Review the TIPS LP investment case.\\"}"}}]}}]}',
        "",
        'data: {"choices":[{"finish_reason":"tool_calls","delta":{}}]}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: Array<Array<{ id: string; inputPreview?: string; label: string; status: string }>> = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onToolActivity: (activities) => {
        snapshots.push(activities.map((activity) => ({
          id: activity.id,
          inputPreview: activity.inputPreview,
          label: activity.label,
          status: activity.status,
        })));
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(snapshots.some((snapshot) =>
      snapshot.some((activity) =>
        activity.id === "call_spawn" &&
        activity.label === "SpawnAgent" &&
        activity.inputPreview?.includes("Task: Review the TIPS LP investment case.") === true,
      ),
    )).toBe(true);
  });

  it("surfaces named subagent lifecycle events as a dedicated roster", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"spawn_started","taskId":"blue","persona":"explore","deliver":"background"}',
        "",
        "event: agent",
        'data: {"type":"child_started","taskId":"blue"}',
        "",
        "event: agent",
        'data: {"type":"child_progress","taskId":"blue","detail":"Searching sources"}',
        "",
        "event: agent",
        'data: {"type":"child_tool_request","taskId":"blue","requestId":"cr_1","toolName":"FileRead"}',
        "",
        "event: agent",
        'data: {"type":"child_completed","taskId":"blue"}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: SubagentActivity[][] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onSubagentActivity: (subagents) => {
        snapshots.push(subagents.map((subagent) => ({ ...subagent })));
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(snapshots[0]?.[0]).toMatchObject({
      taskId: "blue",
      role: "explore",
      status: "running",
    });
    expect(snapshots.some((snapshot) =>
      snapshot.some((subagent) =>
        subagent.taskId === "blue" &&
        subagent.status === "waiting" &&
        subagent.detail === "FileRead",
      ),
    )).toBe(true);
    expect(snapshots.at(-1)?.[0]).toMatchObject({
      taskId: "blue",
      status: "done",
    });
  });

  it("keeps concrete delegated task details for subagents instead of replacing them with iteration noise", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"spawn_started","taskId":"blue","persona":"skeptic-partner","deliver":"return","detail":"Task: Review Naeoe Distillery TIPS LP investment materials and identify market risks."}',
        "",
        "event: agent",
        'data: {"type":"child_started","taskId":"blue","detail":"Task: Review Naeoe Distillery TIPS LP investment materials and identify market risks."}',
        "",
        "event: agent",
        'data: {"type":"child_progress","taskId":"blue","detail":"iteration 1"}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: SubagentActivity[][] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onSubagentActivity: (subagents) => {
        snapshots.push(subagents.map((subagent) => ({ ...subagent })));
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(snapshots.some((snapshot) =>
      snapshot.some((subagent) =>
        subagent.taskId === "blue" &&
        subagent.detail ===
          "Task: Review Naeoe Distillery TIPS LP investment materials and identify market risks.",
      ),
    )).toBe(true);
    expect(snapshots.at(-1)?.[0]?.detail).not.toBe("iteration 1");
  });

  it("dispatches mission events to the mission callback", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"mission_created","mission":{"id":"m1","title":"Draft report","kind":"goal","status":"running","payload":{"private":true}}}',
        "",
        "event: agent",
        'data: {"type":"mission_event","missionId":"m1","eventType":"blocked","message":"Needs approval","payload":{"private":true}}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const events: Array<Record<string, unknown>> = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onMissionEvent: (event) => events.push(event),
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(events).toEqual([
      {
        type: "mission_created",
        mission: {
          id: "m1",
          title: "Draft report",
          kind: "goal",
          status: "running",
          payload: { private: true },
        },
      },
      {
        type: "mission_event",
        missionId: "m1",
        eventType: "blocked",
        message: "Needs approval",
        payload: { private: true },
      },
    ]);
  });

  it("marks background subagents done when spawn_result arrives", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"spawn_started","taskId":"blue","persona":"calculator","deliver":"background"}',
        "",
        "event: agent",
        'data: {"type":"spawn_result","taskId":"blue","status":"ok","toolCallCount":2}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"done"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: SubagentActivity[][] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onSubagentActivity: (subagents) => {
        snapshots.push(subagents.map((subagent) => ({ ...subagent })));
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(snapshots[0]?.[0]).toMatchObject({
      taskId: "blue",
      role: "calculator",
      status: "running",
    });
    expect(snapshots.at(-1)?.[0]).toMatchObject({
      taskId: "blue",
      role: "calculator",
      status: "done",
    });
  });

  it("marks failed background spawn results as terminal subagents", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"spawn_started","taskId":"blue","persona":"calculator","deliver":"background"}',
        "",
        "event: agent",
        'data: {"type":"spawn_result","taskId":"blue","status":"error","errorMessage":"child failed"}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"failed"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: SubagentActivity[][] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onSubagentActivity: (subagents) => {
        snapshots.push(subagents.map((subagent) => ({ ...subagent })));
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(snapshots.at(-1)?.[0]).toMatchObject({
      taskId: "blue",
      status: "error",
      detail: "child failed",
    });
  });

  it("surfaces inspected running background tasks as a dedicated roster", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"background_task","taskId":"task-running","persona":"writer","status":"running","detail":"Drafting chapter 4"}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"still running"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: SubagentActivity[][] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onSubagentActivity: (subagents) => {
        snapshots.push(subagents.map((subagent) => ({ ...subagent })));
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(snapshots.at(-1)?.[0]).toMatchObject({
      taskId: "task-running",
      role: "writer",
      status: "running",
      detail: "Drafting chapter 4",
    });
  });

  it("decodes control events, replay completion, and legacy ask_user", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"control_event","seq":7,"event":{"type":"control_request_created","request":{"requestId":"cr_1","kind":"tool_permission","state":"pending","sessionKey":"agent:main:app:general","channelName":"general","source":"turn","prompt":"Allow Bash?","createdAt":1,"expiresAt":999999}}}',
        "",
        "event: agent",
        'data: {"type":"control_replay_complete","lastSeq":7}',
        "",
        "event: agent",
        'data: {"type":"ask_user","questionId":"turn_1:ask:1","question":"Which file?","choices":[]}',
        "",
        "event: agent",
        'data: {"type":"plan_ready","planId":"plan_1","requestId":"cr_plan","state":"awaiting_approval","plan":"- verify first"}',
        "",
        "event: agent",
        'data: {"type":"text_delta","delta":"hello"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const controlEvents: string[] = [];
    const replaySeqs: number[] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onControlEvent: (event) => {
        if (event.type === "control_request_created") {
          controlEvents.push(event.request.requestId);
        }
      },
      onControlReplayComplete: (lastSeq) => replaySeqs.push(lastSeq),
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(controlEvents).toEqual(["cr_1", "turn_1:ask:1", "cr_plan"]);
    expect(replaySeqs).toEqual([7]);
  });

  it("preserves bounded redacted tool previews from agent events", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"tool_start","id":"tu_1","name":"FileRead","input_preview":"{\\"path\\":\\"book/FINAL_MANUSCRIPT.md\\",\\"Authorization\\":\\"Bearer secret\\"}"}',
        "",
        "event: agent",
        'data: {"type":"tool_end","id":"tu_1","status":"ok","durationMs":20,"output_preview":"read complete token=ghp_supersecret"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: Array<Array<{ id: string; inputPreview?: string; outputPreview?: string; status: string }>> = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onToolActivity: (activities) => {
        snapshots.push(activities.map((activity) => ({
          id: activity.id,
          inputPreview: activity.inputPreview,
          outputPreview: activity.outputPreview,
          status: activity.status,
        })));
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(snapshots).not.toEqual([]);
    expect(snapshots.flat()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "tu_1",
          inputPreview: "{\"path\":\"book/FINAL_MANUSCRIPT.md\",\"Authorization\":\"Bearer [redacted]\"}",
          outputPreview: "read complete token=[redacted]",
          status: "done",
        }),
      ]),
    );
  });

  it("attaches structured patch previews to the matching tool activity", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"tool_start","id":"tu_patch","name":"PatchApply","input_preview":"{\\"patch\\":\\"...\\"}"}',
        "",
        "event: agent",
        'data: {"type":"patch_preview","toolUseId":"tu_patch","dryRun":false,"changedFiles":["src/app.ts"],"createdFiles":[],"deletedFiles":[],"files":[{"path":"src/app.ts","operation":"update","hunks":1,"addedLines":1,"removedLines":1}]}',
        "",
        "event: agent",
        'data: {"type":"tool_end","id":"tu_patch","status":"ok","durationMs":20,"output_preview":"{\\"changedFiles\\":[\\"src/app.ts\\"]}"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const snapshots: Array<Array<unknown>> = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onToolActivity: (activities) => {
        snapshots.push(activities);
      },
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    const patchActivity = snapshots.flat().find((activity) =>
      typeof activity === "object" &&
      activity !== null &&
      (activity as { id?: string }).id === "tu_patch",
    ) as { patchPreview?: { changedFiles?: string[]; files?: Array<{ path?: string }> } } | undefined;
    expect(patchActivity?.patchPreview).toMatchObject({
      changedFiles: ["src/app.ts"],
      files: [
        {
          path: "src/app.ts",
        },
      ],
    });
  });

  it("maps structured turn_interrupted events to the aborted live phase", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"turn_interrupted","turnId":"turn-1","handoffRequested":true,"source":"web"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const phases: string[] = [];

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onTurnPhase: (phase) => phases.push(phase),
      onDone: () => {},
      onError: (error) => {
        throw error;
      },
    });

    expect(phases).toEqual(["aborted"]);
  });

  it("surfaces a terminal agent error instead of claiming success", async () => {
    mockSseFetch(
      [
        "event: agent",
        'data: {"type":"error","message":"runtime exploded"}',
        "",
        "data: [DONE]",
        "",
      ].join("\n"),
    );

    const onError = vi.fn();
    const onDone = vi.fn();

    await sendMessage("bot-abc", "general", [], {
      onDelta: () => {},
      onDone,
      onError,
    });

    expect(onDone).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]?.[0]).toBeInstanceOf(Error);
    expect(onError.mock.calls[0]?.[0]?.message).toContain("runtime exploded");
  });
});

describe("control request helpers", () => {
  const request: ControlRequestRecord = {
    requestId: "cr_1",
    kind: "tool_permission",
    state: "pending",
    sessionKey: "agent:main:app:general",
    channelName: "general",
    source: "turn",
    prompt: "Allow Bash?",
    createdAt: 1,
    expiresAt: Date.now() + 60_000,
  };

  it("fetchControlRequests calls chat-proxy with session and channel scope", async () => {
    const fetchMock = mockFetch(200, { requests: [request] });
    const out = await fetchControlRequests(
      "bot-abc",
      "agent:main:app:general",
      "general",
    );

    expect(out).toEqual([request]);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/chat/bot-abc/control-requests?");
    expect(url).toContain("sessionKey=agent%3Amain%3Aapp%3Ageneral");
    expect(url).toContain("channelName=general");
  });

  it("fetchControlEvents replays durable control events through chat-proxy", async () => {
    const fetchMock = mockFetch(200, {
      lastSeq: 7,
      events: [
        {
          type: "control_request_created",
          request,
        },
      ],
    });

    const out = await fetchControlEvents(
      "bot-abc",
      "agent:main:app:general",
      "general",
      3,
    );

    expect(out).toEqual({
      lastSeq: 7,
      events: [{ type: "control_request_created", request }],
    });
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/chat/bot-abc/control-events?");
    expect(url).toContain("lastSeq=3");
  });

  it("respondToControlRequest posts the decision and returns resolved request", async () => {
    const resolved = { ...request, state: "approved", decision: "approved" };
    const fetchMock = mockFetch(200, { request: resolved });

    const out = await respondToControlRequest("bot-abc", request, {
      decision: "approved",
      feedback: "ok",
    });

    expect(out).toEqual(resolved);
    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/chat/bot-abc/control-requests/cr_1/response?");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body as string)).toMatchObject({
      decision: "approved",
      sessionKey: "agent:main:app:general",
      feedback: "ok",
    });
  });

  it("respondToControlRequest preserves a legacy ask_user card when the runtime only returns ok", async () => {
    const legacyQuestion: ControlRequestRecord = {
      ...request,
      requestId: "turn_1:ask:1",
      turnId: "turn_1",
      kind: "user_question",
      prompt: "Phase 2 진행 방식을 선택해주세요:",
      proposedInput: {
        choices: [
          { id: "direct", label: "직접 분석" },
          { id: "regenerate", label: "Bull/Bear artifact 재생성 후 진행" },
        ],
      },
    };
    const fetchMock = mockFetch(200, { ok: true });

    const out = await respondToControlRequest("bot-abc", legacyQuestion, {
      decision: "answered",
      answer: "regenerate",
    });

    expect(out).toMatchObject({
      requestId: "turn_1:ask:1",
      kind: "user_question",
      state: "answered",
      decision: "answered",
      answer: "regenerate",
      prompt: "Phase 2 진행 방식을 선택해주세요:",
      proposedInput: legacyQuestion.proposedInput,
    });
    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/chat/bot-abc/control-requests/turn_1%3Aask%3A1/response?");
    expect(JSON.parse(opts.body as string)).toMatchObject({
      decision: "answered",
      answer: "regenerate",
      selectedId: "regenerate",
      sessionKey: "agent:main:app:general",
    });
  });
});
