import http from "node:http";
import type { AddressInfo } from "node:net";
import { describe, expect, it } from "vitest";
import type { LLMMessage } from "./LLMClient.js";
import { LLMClient, normalizeToolUseIdsForRequest } from "./LLMClient.js";

function splitAtNeedleByte(value: string, needle: string, byteOffsetInNeedle: number): [Buffer, Buffer] {
  const bytes = Buffer.from(value, "utf8");
  const needleBytes = Buffer.from(needle, "utf8");
  const needleStart = bytes.indexOf(needleBytes);
  if (needleStart < 0) throw new Error(`needle not found: ${needle}`);
  const splitAt = needleStart + byteOffsetInNeedle;
  return [bytes.subarray(0, splitAt), bytes.subarray(splitAt)];
}

describe("normalizeToolUseIdsForRequest", () => {
  it("normalizes invalid tool_use ids and keeps matching tool_result references aligned", () => {
    const messages: LLMMessage[] = [
      {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: "toolu_01Ca:bad.id+$",
            name: "ReadSpreadsheet",
            input: { path: "sales.xlsx" },
          },
        ],
      },
      {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "toolu_01Ca:bad.id+$",
            content: "ok",
          },
        ],
      },
    ];

    const normalized = normalizeToolUseIdsForRequest(messages);
    const assistant = normalized[0];
    const user = normalized[1];

    expect(assistant).toMatchObject({
      role: "assistant",
      content: [
        {
          type: "tool_use",
          id: "toolu_01Ca_bad_id__",
          name: "ReadSpreadsheet",
          input: { path: "sales.xlsx" },
        },
      ],
    });
    expect(user).toMatchObject({
      role: "user",
      content: [
        {
          type: "tool_result",
          tool_use_id: "toolu_01Ca_bad_id__",
          content: "ok",
        },
      ],
    });
  });

  it("keeps already-valid ids unchanged", () => {
    const messages: LLMMessage[] = [
      {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: "toolu_valid_123",
            name: "Bash",
            input: {},
          },
        ],
      },
    ];

    const normalized = normalizeToolUseIdsForRequest(messages);
    const assistant = normalized[0];

    expect(assistant).toMatchObject({
      role: "assistant",
      content: [
        {
          type: "tool_use",
          id: "toolu_valid_123",
          name: "Bash",
          input: {},
        },
      ],
    });
  });

  it("deduplicates sanitized collisions deterministically", () => {
    const messages: LLMMessage[] = [
      {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: "call.one",
            name: "ToolA",
            input: {},
          },
          {
            type: "tool_use",
            id: "call:one",
            name: "ToolB",
            input: {},
          },
        ],
      },
      {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: "call.one", content: "a" },
          { type: "tool_result", tool_use_id: "call:one", content: "b" },
        ],
      },
    ];

    const normalized = normalizeToolUseIdsForRequest(messages);
    const assistant = normalized[0];
    const user = normalized[1];

    expect(assistant).toMatchObject({
      role: "assistant",
      content: [
        { type: "tool_use", id: "call_one", name: "ToolA", input: {} },
        { type: "tool_use", id: "call_one_1", name: "ToolB", input: {} },
      ],
    });
    expect(user).toMatchObject({
      role: "user",
      content: [
        { type: "tool_result", tool_use_id: "call_one", content: "a" },
        { type: "tool_result", tool_use_id: "call_one_1", content: "b" },
      ],
    });
  });

  it("allocates fresh canonical ids for repeated raw tool_use ids across turns", () => {
    const messages: LLMMessage[] = [
      {
        role: "assistant",
        content: [{ type: "tool_use", id: "functions.TaskOutput:0", name: "TaskOutput", input: {} }],
      },
      {
        role: "user",
        content: [{ type: "tool_result", tool_use_id: "functions.TaskOutput:0", content: "first" }],
      },
      {
        role: "assistant",
        content: [{ type: "tool_use", id: "functions.TaskOutput:0", name: "TaskOutput", input: {} }],
      },
      {
        role: "user",
        content: [{ type: "tool_result", tool_use_id: "functions.TaskOutput:0", content: "second" }],
      },
    ];

    const normalized = normalizeToolUseIdsForRequest(messages);

    expect(normalized[0]).toMatchObject({
      content: [{ type: "tool_use", id: "functions_TaskOutput_0" }],
    });
    expect(normalized[1]).toMatchObject({
      content: [{ type: "tool_result", tool_use_id: "functions_TaskOutput_0" }],
    });
    expect(normalized[2]).toMatchObject({
      content: [{ type: "tool_use", id: "functions_TaskOutput_0_1" }],
    });
    expect(normalized[3]).toMatchObject({
      content: [{ type: "tool_result", tool_use_id: "functions_TaskOutput_0_1" }],
    });
  });
});

describe("LLMClient.stream abort", () => {
  it("honors abort while consuming an HTTP error body", async () => {
    const server = http.createServer((_req, res) => {
      res.writeHead(500, { "Content-Type": "text/plain" });
      res.write("partial error body");
    });
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", resolve);
    });

    try {
      const { port } = server.address() as AddressInfo;
      const client = new LLMClient({
        apiProxyUrl: `http://127.0.0.1:${port}`,
        gatewayToken: "test-token",
        defaultModel: "test-model",
      });
      const controller = new AbortController();
      const next = client
        .stream({
          messages: [{ role: "user", content: "hello" }],
          signal: controller.signal,
        })
        .next();

      await new Promise((resolve) => setTimeout(resolve, 20));
      controller.abort(new Error("user_interrupt"));

      await expect(
        Promise.race([
          next,
          new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 500)),
        ]),
      ).rejects.toThrow("user_interrupt");
    } finally {
      await new Promise<void>((resolve) => {
        server.close(() => resolve());
      });
    }
  });
});

describe("LLMClient.resolveRuntimeModel", () => {
  it("reads the current bot runtime model from api-proxy", async () => {
    const server = http.createServer((req, res) => {
      expect(req.url).toBe("/v1/bot-model");
      expect(req.headers["x-api-key"]).toBe("gw-token");
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ runtimeModel: "openai/gpt-5.5" }));
    });

    try {
      await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("server did not bind to a TCP port");

      const client = new LLMClient({
        apiProxyUrl: `http://127.0.0.1:${address.port}`,
        gatewayToken: "gw-token",
        defaultModel: "anthropic/claude-sonnet-4-6",
      });

      await expect(client.resolveRuntimeModel("anthropic/claude-sonnet-4-6")).resolves.toBe("openai/gpt-5.5");
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });

  it("falls back to the provisioned model when api-proxy cannot resolve dynamically", async () => {
    const server = http.createServer((_req, res) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ dynamic: false, reason: "not_platform_credits" }));
    });

    try {
      await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("server did not bind to a TCP port");

      const client = new LLMClient({
        apiProxyUrl: `http://127.0.0.1:${address.port}`,
        gatewayToken: "gw-token",
        defaultModel: "anthropic/claude-sonnet-4-6",
      });

      await expect(client.resolveRuntimeModel("anthropic/claude-sonnet-4-6")).resolves.toBe("anthropic/claude-sonnet-4-6");
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
});

describe("LLMClient SSE UTF-8 decoding", () => {
  it("preserves Korean text when Anthropic SSE chunks split a UTF-8 character", async () => {
    const korean = "프롬프트";
    const frame = [
      "event: content_block_delta",
      `data: ${JSON.stringify({ type: "content_block_delta", index: 0, delta: { type: "text_delta", text: korean } })}`,
      "",
      "event: message_delta",
      `data: ${JSON.stringify({ type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { output_tokens: 1 } })}`,
      "",
      "event: message_stop",
      `data: ${JSON.stringify({ type: "message_stop" })}`,
      "",
    ].join("\n");
    const [first, second] = splitAtNeedleByte(frame, "트", 1);
    const server = http.createServer((req, res) => {
      req.resume();
      req.on("end", () => {
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.write(first);
        setTimeout(() => res.end(second), 5);
      });
    });

    try {
      await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("server did not bind to a TCP port");

      const client = new LLMClient({
        apiProxyUrl: `http://127.0.0.1:${address.port}`,
        gatewayToken: "gw-token",
        defaultModel: "claude-sonnet-4-6",
      });

      const deltas: string[] = [];
      for await (const event of client.stream({ messages: [{ role: "user", content: "hi" }] })) {
        if (event.kind === "text_delta") deltas.push(event.delta);
      }

      expect(deltas.join("")).toBe(korean);
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
});

describe("LLMClient Codex OAuth forwarding", () => {
  it("forwards Codex OAuth headers for openai-codex models", async () => {
    let seenHeaders: http.IncomingHttpHeaders | null = null;
    const server = http.createServer((req, res) => {
      seenHeaders = req.headers;
      req.resume();
      req.on("end", () => {
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.end('event: message_stop\ndata: {"type":"message_stop"}\n\n');
      });
    });

    try {
      await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("server did not bind to a TCP port");

      const client = new LLMClient({
        apiProxyUrl: `http://127.0.0.1:${address.port}`,
        gatewayToken: "gw-token",
        codexAccessToken: "codex-access",
        codexRefreshToken: "codex-refresh",
        defaultModel: "openai-codex/gpt-5.5",
      });

      for await (const event of client.stream({ messages: [{ role: "user", content: "hi" }] })) {
        void event;
        // Drain the stream.
      }

      expect(seenHeaders?.["x-api-key"]).toBe("gw-token");
      expect(seenHeaders?.["x-openai-codex-access-token"]).toBe("codex-access");
      expect(seenHeaders?.["x-openai-codex-refresh-token"]).toBe("codex-refresh");
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
});

describe("LLMClient provider health metadata", () => {
  it("retains provider health headers from api-proxy responses", async () => {
    const server = http.createServer((req, res) => {
      req.resume();
      req.on("end", () => {
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "x-magi-provider-health-provider": "openai",
          "x-magi-provider-health-model": "gpt-5.4-mini",
          "x-magi-provider-health-state": "degraded",
          "x-magi-provider-health-confidence": "high",
          "x-magi-provider-health-summary": "local-rate-limit",
          "x-magi-provider-health-route": "provider_health_fallback",
        });
        res.end('event: message_stop\ndata: {"type":"message_stop"}\n\n');
      });
    });

    try {
      await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("server did not bind to a TCP port");

      const client = new LLMClient({
        apiProxyUrl: `http://127.0.0.1:${address.port}`,
        gatewayToken: "gw-token",
        defaultModel: "gpt-5.4-mini",
      });

      for await (const event of client.stream({ messages: [{ role: "user", content: "hi" }] })) {
        void event;
      }

      expect(client.getLastProviderHealth()).toMatchObject({
        provider: "openai",
        model: "gpt-5.4-mini",
        state: "degraded",
        confidence: "high",
        routeReason: "provider_health_fallback",
      });
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
});
