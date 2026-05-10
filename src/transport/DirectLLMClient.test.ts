import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import { DirectLLMClient } from "./DirectLLMClient.js";

let server: ReturnType<typeof createServer> | null = null;

function splitAtNeedleByte(value: string, needle: string, byteOffsetInNeedle: number): [Buffer, Buffer] {
  const bytes = Buffer.from(value, "utf8");
  const needleBytes = Buffer.from(needle, "utf8");
  const needleStart = bytes.indexOf(needleBytes);
  if (needleStart < 0) throw new Error(`needle not found: ${needle}`);
  const splitAt = needleStart + byteOffsetInNeedle;
  return [bytes.subarray(0, splitAt), bytes.subarray(splitAt)];
}

afterEach(async () => {
  if (!server) return;
  await new Promise<void>((resolve) => server?.close(() => resolve()));
  server = null;
});

async function startServer(
  handler: (req: IncomingMessage, res: ServerResponse) => void,
): Promise<string> {
  server = createServer(handler);
  await new Promise<void>((resolve) => server?.listen(0, "127.0.0.1", () => resolve()));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("missing server address");
  return `http://127.0.0.1:${address.port}`;
}

describe("DirectLLMClient", () => {
  it("calls Anthropic-compatible endpoints directly", async () => {
    let body = "";
    const baseUrl = await startServer((req, res) => {
      expect(req.url).toBe("/v1/messages");
      expect(req.headers["x-api-key"]).toBe("sk-ant-test");
      req.on("data", (chunk) => {
        body += chunk;
      });
      req.on("end", () => {
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.end([
          "event: content_block_delta",
          'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}',
          "",
          "event: message_delta",
          'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}',
          "",
          "event: message_stop",
          'data: {"type":"message_stop"}',
          "",
        ].join("\n"));
      });
    });

    const client = new DirectLLMClient({
      providers: {
        anthropic: { kind: "anthropic", baseUrl, apiKey: "sk-ant-test" },
      },
    });

    const events = [];
    for await (const evt of client.stream({
      model: "claude-opus-4-7",
      messages: [{ role: "user", content: "hi" }],
    })) {
      events.push(evt);
    }

    expect(JSON.parse(body).model).toBe("claude-opus-4-7");
    expect(events).toContainEqual({ kind: "text_delta", blockIndex: 0, delta: "ok" });
  });

  it("calls OpenAI-compatible endpoints and normalizes text SSE", async () => {
    let body = "";
    const baseUrl = await startServer((req, res) => {
      expect(req.url).toBe("/v1/chat/completions");
      expect(req.headers.authorization).toBe("Bearer sk-openai-test");
      req.on("data", (chunk) => {
        body += chunk;
      });
      req.on("end", () => {
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.end([
          'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}',
          "",
          'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1}}',
          "",
          "data: [DONE]",
          "",
        ].join("\n"));
      });
    });

    const client = new DirectLLMClient({
      providers: {
        openai: { kind: "openai-compatible", baseUrl, apiKey: "sk-openai-test" },
      },
    });

    const events = [];
    for await (const evt of client.stream({
      model: "gpt-5-nano",
      system: "You are terse.",
      messages: [{ role: "user", content: "hi" }],
    })) {
      events.push(evt);
    }

    expect(JSON.parse(body).messages[0]).toEqual({ role: "system", content: "You are terse." });
    expect(events).toContainEqual({ kind: "text_delta", blockIndex: 0, delta: "hello" });
    expect(events).toContainEqual({
      kind: "message_end",
      stopReason: "end_turn",
      usage: { inputTokens: 2, outputTokens: 1 },
    });
  });

  it("supports no-auth OpenAI-compatible local providers", async () => {
    let authorizationHeader: string | string[] | undefined;
    const baseUrl = await startServer((req, res) => {
      authorizationHeader = req.headers.authorization;
      req.resume();
      req.on("end", () => {
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.end([
          'data: {"choices":[{"delta":{"content":"local"},"finish_reason":null}]}',
          "",
          'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}',
          "",
          "data: [DONE]",
          "",
        ].join("\n"));
      });
    });

    const client = new DirectLLMClient({
      providers: {
        openai: { kind: "openai-compatible", baseUrl },
      },
    });

    const deltas: string[] = [];
    for await (const evt of client.stream({
      model: "llama3.1",
      messages: [{ role: "user", content: "hi" }],
    })) {
      if (evt.kind === "text_delta") deltas.push(evt.delta);
    }

    expect(authorizationHeader).toBeUndefined();
    expect(deltas.join("")).toBe("local");
  });

  it("routes ollama-prefixed models to no-auth local providers without OpenAI stream options", async () => {
    let body = "";
    const baseUrl = await startServer((req, res) => {
      expect(req.url).toBe("/v1/chat/completions");
      expect(req.headers.authorization).toBeUndefined();
      req.on("data", (chunk) => {
        body += chunk;
      });
      req.on("end", () => {
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.end([
          'data: {"choices":[{"delta":{"content":"local-ok"},"finish_reason":"stop"}]}',
          "",
          "data: [DONE]",
          "",
        ].join("\n"));
      });
    });

    const client = new DirectLLMClient({
      providers: {
        ollama: { kind: "openai-compatible", baseUrl },
      },
    });

    const events = [];
    for await (const evt of client.stream({
      model: "ollama/llama3.3:70b",
      messages: [{ role: "user", content: "hi" }],
    })) {
      events.push(evt);
    }

    expect(JSON.parse(body).model).toBe("llama3.3:70b");
    expect(JSON.parse(body).stream_options).toBeUndefined();
    expect(events).toContainEqual({ kind: "text_delta", blockIndex: 0, delta: "local-ok" });
  });

  it("aborts pending direct HTTP requests before the upstream response starts", async () => {
    const baseUrl = await startServer((req, res) => {
      req.resume();
      req.on("end", () => {
        setTimeout(() => {
          if (!res.destroyed) {
            res.writeHead(200, { "content-type": "text/event-stream" });
            res.end("data: [DONE]\n\n");
          }
        }, 200);
      });
    });
    const controller = new AbortController();
    const client = new DirectLLMClient({
      providers: {
        openai: { kind: "openai-compatible", baseUrl },
      },
    });
    const events = client.stream({
      model: "gpt-5-nano",
      messages: [{ role: "user", content: "hi" }],
      signal: controller.signal,
    });

    controller.abort(new Error("stop-now"));

    await expect(events.next()).rejects.toThrow("stop-now");
  });

  it("aborts active OpenAI-compatible streams after partial output", async () => {
    const baseUrl = await startServer((req, res) => {
      req.resume();
      req.on("end", () => {
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.write('data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\n');
      });
    });
    const controller = new AbortController();
    const client = new DirectLLMClient({
      providers: {
        openai: { kind: "openai-compatible", baseUrl },
      },
    });
    const events = client.stream({
      model: "gpt-5-nano",
      messages: [{ role: "user", content: "hi" }],
      signal: controller.signal,
    });

    await expect(events.next()).resolves.toMatchObject({
      value: { kind: "text_delta", delta: "hello" },
    });
    controller.abort(new Error("stream-stop"));

    await expect(events.next()).rejects.toThrow("stream-stop");
  });

  it("preserves Korean text when OpenAI-compatible SSE chunks split a UTF-8 character", async () => {
    const korean = "프롬프트";
    const frame = [
      `data: ${JSON.stringify({ choices: [{ delta: { content: korean }, finish_reason: null }] })}`,
      "",
      `data: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 2, completion_tokens: 1 } })}`,
      "",
      "data: [DONE]",
      "",
    ].join("\n");
    const [first, second] = splitAtNeedleByte(frame, "트", 1);
    const baseUrl = await startServer((req, res) => {
      req.resume();
      req.on("end", () => {
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.write(first);
        setTimeout(() => res.end(second), 5);
      });
    });

    const client = new DirectLLMClient({
      providers: {
        openai: { kind: "openai-compatible", baseUrl, apiKey: "sk-openai-test" },
      },
    });

    const deltas: string[] = [];
    for await (const evt of client.stream({
      model: "gpt-5-nano",
      messages: [{ role: "user", content: "hi" }],
    })) {
      if (evt.kind === "text_delta") deltas.push(evt.delta);
    }

    expect(deltas.join("")).toBe(korean);
  });

  it("maps OpenAI-compatible tool schemas and streamed tool calls", async () => {
    let body = "";
    const baseUrl = await startServer((req, res) => {
      expect(req.url).toBe("/v1/chat/completions");
      req.on("data", (chunk) => {
        body += chunk;
      });
      req.on("end", () => {
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.end([
          'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"Bash","arguments":"{\\"cmd\\":"}}]},"finish_reason":null}]}',
          "",
          'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"ls\\"}"}}]},"finish_reason":null}]}',
          "",
          'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":3,"completion_tokens":2}}',
          "",
          "data: [DONE]",
          "",
        ].join("\n"));
      });
    });

    const client = new DirectLLMClient({
      providers: {
        openai: { kind: "openai-compatible", baseUrl, apiKey: "sk-openai-test" },
      },
    });

    const events = [];
    for await (const evt of client.stream({
      model: "gpt-5.4-mini",
      messages: [{ role: "user", content: "list files" }],
      tools: [{
        name: "Bash",
        description: "Run a shell command",
        input_schema: {
          type: "object",
          properties: { cmd: { type: "string" } },
          required: ["cmd"],
        },
      }],
    })) {
      events.push(evt);
    }

    const parsed = JSON.parse(body);
    expect(parsed.tools[0]).toEqual({
      type: "function",
      function: {
        name: "Bash",
        description: "Run a shell command",
        parameters: {
          type: "object",
          properties: { cmd: { type: "string" } },
          required: ["cmd"],
        },
      },
    });
    expect(events).toContainEqual({
      kind: "tool_use_start",
      blockIndex: 1,
      id: "call_1",
      name: "Bash",
    });
    expect(events).toContainEqual({ kind: "tool_use_input_delta", blockIndex: 1, partial: "{\"cmd\":" });
    expect(events).toContainEqual({ kind: "tool_use_input_delta", blockIndex: 1, partial: "\"ls\"}" });
    expect(events).toContainEqual({ kind: "block_stop", blockIndex: 1 });
    expect(events).toContainEqual({
      kind: "message_end",
      stopReason: "tool_use",
      usage: { inputTokens: 3, outputTokens: 2 },
    });
  });

  it("preserves OpenAI-compatible base paths such as Google v1beta/openai", async () => {
    const baseUrl = await startServer((req, res) => {
      expect(req.url).toBe("/v1beta/openai/chat/completions");
      req.resume();
      req.on("end", () => {
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.end([
          'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
          "",
          "data: [DONE]",
          "",
        ].join("\n"));
      });
    });

    const client = new DirectLLMClient({
      providers: {
        google: {
          kind: "openai-compatible",
          baseUrl: `${baseUrl}/v1beta/openai`,
          apiKey: "sk-google-test",
        },
      },
    });

    const events = [];
    for await (const evt of client.stream({
      model: "gemini-3.1-pro-preview",
      messages: [{ role: "user", content: "hi" }],
    })) {
      events.push(evt);
    }

    expect(events).toContainEqual({ kind: "text_delta", blockIndex: 0, delta: "ok" });
  });
});
