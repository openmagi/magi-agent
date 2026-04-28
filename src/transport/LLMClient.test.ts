import http from "node:http";
import type { AddressInfo } from "node:net";
import { describe, expect, it } from "vitest";
import type { LLMMessage } from "./LLMClient.js";
import { LLMClient, normalizeToolUseIdsForRequest } from "./LLMClient.js";

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
          "x-clawy-provider-health-provider": "openai",
          "x-clawy-provider-health-model": "gpt-5.4-mini",
          "x-clawy-provider-health-state": "degraded",
          "x-clawy-provider-health-confidence": "high",
          "x-clawy-provider-health-summary": "local-rate-limit",
          "x-clawy-provider-health-route": "provider_health_fallback",
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
