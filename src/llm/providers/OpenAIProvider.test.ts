import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import { OpenAIProvider } from "./OpenAIProvider.js";

let server: ReturnType<typeof createServer> | null = null;

afterEach(async () => {
  if (!server) return;
  await new Promise<void>((resolve) => server?.close(() => resolve()));
  server = null;
});

describe("OpenAIProvider", () => {
  it("omits Authorization when connecting to no-auth local OpenAI-compatible servers", async () => {
    let authorizationHeader: string | string[] | undefined;
    server = createServer((req, res) => {
      authorizationHeader = req.headers.authorization;
      req.resume();
      req.on("end", () => {
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.end([
          'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
          "",
          'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}',
          "",
          "data: [DONE]",
          "",
        ].join("\n"));
      });
    });
    await new Promise<void>((resolve) => server?.listen(0, "127.0.0.1", resolve));
    const address = server.address() as AddressInfo;

    const provider = new OpenAIProvider({
      baseUrl: `http://127.0.0.1:${address.port}`,
      defaultModel: "llama3.1",
    });

    const events = [];
    for await (const event of provider.stream({
      messages: [{ role: "user", content: "hi" }],
    })) {
      events.push(event);
    }

    expect(authorizationHeader).toBeUndefined();
    expect(events).toContainEqual({ kind: "text_delta", blockIndex: 0, delta: "ok" });
    expect(events).toContainEqual({
      kind: "message_end",
      stopReason: "end_turn",
      usage: { inputTokens: 1, outputTokens: 1 },
    });
  });
});
