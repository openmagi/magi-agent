import { describe, expect, it } from "vitest";
import type { ServerResponse } from "node:http";
import { SseWriter } from "./SseWriter.js";

class CaptureResponse {
  readonly chunks: string[] = [];
  writeHead(): void {}
  write(chunk: string): boolean {
    this.chunks.push(chunk);
    return true;
  }
  end(): void {}
}

function makeWriter(): { writer: SseWriter; response: CaptureResponse } {
  const response = new CaptureResponse();
  return {
    writer: new SseWriter(response as unknown as ServerResponse),
    response,
  };
}

function agentPayloads(response: CaptureResponse): unknown[] {
  return response.chunks
    .filter((chunk) => chunk.startsWith("event: agent\n"))
    .map((chunk) => {
      const line = chunk.split("\n").find((part) => part.startsWith("data: "));
      if (!line) throw new Error(`missing data line: ${chunk}`);
      return JSON.parse(line.slice("data: ".length)) as unknown;
    });
}

describe("SseWriter", () => {
  it("streams a leading route META tag once to clients", () => {
    const { writer, response } = makeWriter();

    writer.agent({ type: "text_delta", delta: "[META: intent=대화, " });
    writer.agent({ type: "text_delta", delta: "domain=일상, route=direct]" });
    writer.agent({ type: "text_delta", delta: "\n\n안녕하세요!" });

    const text = agentPayloads(response)
      .filter((event): event is { type: string; delta: string } =>
        typeof event === "object" &&
        event !== null &&
        (event as { type?: unknown }).type === "text_delta",
      )
      .map((event) => event.delta)
      .join("");
    expect(text).toBe("[META: intent=대화, domain=일상, route=direct]\n\n안녕하세요!");
    expect(text.match(/\[META:/g)).toHaveLength(1);
  });

  it("streams normal text without waiting for a META tag", () => {
    const { writer, response } = makeWriter();

    writer.agent({ type: "text_delta", delta: "Hello" });
    writer.agent({ type: "text_delta", delta: " world" });

    const text = agentPayloads(response)
      .filter((event): event is { type: string; delta: string } =>
        typeof event === "object" &&
        event !== null &&
        (event as { type?: unknown }).type === "text_delta",
      )
      .map((event) => event.delta)
      .join("");
    expect(text).toBe("Hello world");
  });

  it("streams only the first embedded route META tag to clients", () => {
    const { writer, response } = makeWriter();

    writer.agent({ type: "text_delta", delta: "좋아. " });
    writer.agent({ type: "text_delta", delta: "[META: intent=실행, " });
    writer.agent({ type: "text_delta", delta: "domain=문서작성, complexity=complex, route=subagent]" });
    writer.agent({ type: "text_delta", delta: "[META: route=direct]바로 시작합니다." });

    const text = agentPayloads(response)
      .filter((event): event is { type: string; delta: string } =>
        typeof event === "object" &&
        event !== null &&
        (event as { type?: unknown }).type === "text_delta",
      )
      .map((event) => event.delta)
      .join("");
    expect(text).toBe(
      "좋아. [META: intent=실행, domain=문서작성, complexity=complex, route=subagent]바로 시작합니다.",
    );
    expect(text.match(/\[META:/g)).toHaveLength(1);
  });
});
