/**
 * LLMStreamReader unit tests (R3 refactor).
 *
 * Exercise the stream-assembly layer in isolation — feed canned
 * LLMEvent sequences and assert the returned blocks / stopReason /
 * usage.
 */

import { describe, it, expect } from "vitest";
import type { ServerResponse } from "node:http";
import { readOne } from "./LLMStreamReader.js";
import type {
  LLMClient,
  LLMEvent,
  LLMStreamRequest,
} from "../transport/LLMClient.js";
import { SseWriter } from "../transport/SseWriter.js";

class FakeSse extends SseWriter {
  readonly events: unknown[] = [];
  public deltaChars = 0;
  constructor() {
    super({
      writeHead: () => {},
      write: () => true,
      end: () => {},
    } as unknown as ServerResponse);
  }
  override agent(event: unknown): void {
    this.events.push(event);
  }
  override legacyDelta(s: string): void {
    this.deltaChars += s.length;
  }
  override legacyFinish(): void {}
  override start(): void {}
  override end(): void {}
}

function makeLLM(events: LLMEvent[]): LLMClient {
  const client = {
    async *stream(_req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
      for (const evt of events) yield evt;
    },
  };
  return client as unknown as LLMClient;
}

describe("LLMStreamReader.readOne", () => {
  it("assembles a text block from text_delta events", async () => {
    const sse = new FakeSse();
    const llm = makeLLM([
      { kind: "text_delta", blockIndex: 0, delta: "Hello " },
      { kind: "text_delta", blockIndex: 0, delta: "world." },
      { kind: "block_stop", blockIndex: 0 },
      {
        kind: "message_end",
        stopReason: "end_turn",
        usage: { inputTokens: 10, outputTokens: 5 },
      },
    ]);
    const out = await readOne(
      { llm, model: "test", sse, onError: () => {} },
      "",
      [],
      [],
    );
    expect(out.blocks).toEqual([{ type: "text", text: "Hello world." }]);
    expect(out.stopReason).toBe("end_turn");
    expect(out.usage).toEqual({ inputTokens: 10, outputTokens: 5 });
    // text_delta now fires exclusively on the `event: agent` channel;
    // legacyDelta must stay silent to avoid the dual-render bug.
    expect(sse.deltaChars).toBe(0);
    const textDeltas = sse.events.filter(
      (e): e is { type: string; delta: string } =>
        typeof e === "object" && e !== null && (e as { type?: string }).type === "text_delta",
    );
    expect(textDeltas.map((e) => e.delta).join("")).toBe("Hello world.");
  });

  it("preserves thinking block with signature (T4-18)", async () => {
    const sse = new FakeSse();
    const llm = makeLLM([
      { kind: "thinking_delta", blockIndex: 0, delta: "step1 " },
      { kind: "thinking_delta", blockIndex: 0, delta: "step2" },
      { kind: "thinking_signature", blockIndex: 0, signature: "sig-xyz" },
      { kind: "block_stop", blockIndex: 0 },
      { kind: "text_delta", blockIndex: 1, delta: "done." },
      { kind: "block_stop", blockIndex: 1 },
      {
        kind: "message_end",
        stopReason: "end_turn",
        usage: { inputTokens: 1, outputTokens: 1 },
      },
    ]);
    const out = await readOne(
      { llm, model: "test", sse, onError: () => {} },
      "",
      [],
      [],
    );
    expect(out.blocks.length).toBe(2);
    expect(out.blocks[0]).toEqual({
      type: "thinking",
      thinking: "step1 step2",
      signature: "sig-xyz",
    });
    expect(out.blocks[1]).toEqual({ type: "text", text: "done." });
  });

  it("assembles tool_use block with streamed input JSON", async () => {
    const sse = new FakeSse();
    const llm = makeLLM([
      {
        kind: "tool_use_start",
        blockIndex: 0,
        id: "tu_1",
        name: "Bash",
      },
      {
        kind: "tool_use_input_delta",
        blockIndex: 0,
        partial: '{"cmd":',
      },
      {
        kind: "tool_use_input_delta",
        blockIndex: 0,
        partial: '"ls"}',
      },
      { kind: "block_stop", blockIndex: 0 },
      {
        kind: "message_end",
        stopReason: "tool_use",
        usage: { inputTokens: 1, outputTokens: 1 },
      },
    ]);
    const out = await readOne(
      { llm, model: "test", sse, onError: () => {} },
      "",
      [],
      [],
    );
    expect(out.stopReason).toBe("tool_use");
    expect(out.blocks).toEqual([
      { type: "tool_use", id: "tu_1", name: "Bash", input: { cmd: "ls" } },
    ]);
  });

  it("marks malformed tool input JSON without throwing", async () => {
    const sse = new FakeSse();
    const llm = makeLLM([
      { kind: "tool_use_start", blockIndex: 0, id: "tu_1", name: "Bash" },
      { kind: "tool_use_input_delta", blockIndex: 0, partial: "{not-json" },
      { kind: "block_stop", blockIndex: 0 },
      {
        kind: "message_end",
        stopReason: "tool_use",
        usage: { inputTokens: 1, outputTokens: 1 },
      },
    ]);
    const out = await readOne(
      { llm, model: "test", sse, onError: () => {} },
      "",
      [],
      [],
    );
    const block = out.blocks[0] as { type: string; input: Record<string, unknown> };
    expect(block.type).toBe("tool_use");
    expect(block.input._malformed).toBe(true);
    expect(block.input._raw).toBe("{not-json");
  });

  it("propagates error events via onError and throws", async () => {
    const sse = new FakeSse();
    const calls: Array<{ code: string; err: unknown }> = [];
    const llm = makeLLM([
      { kind: "error", code: "rate_limited", message: "too many requests" },
    ]);
    await expect(
      readOne(
        { llm, model: "test", sse, onError: (code, err) => calls.push({ code, err }) },
        "",
        [],
        [],
      ),
    ).rejects.toThrow(/rate_limited: too many requests/);
    expect(calls.length).toBe(1);
    expect(calls[0]?.code).toBe("rate_limited");
  });

  it("preserves block order when mixing text + thinking + tool_use", async () => {
    const sse = new FakeSse();
    const llm = makeLLM([
      { kind: "thinking_delta", blockIndex: 0, delta: "reasoning" },
      { kind: "thinking_signature", blockIndex: 0, signature: "sig-1" },
      { kind: "block_stop", blockIndex: 0 },
      { kind: "text_delta", blockIndex: 1, delta: "ok so " },
      { kind: "block_stop", blockIndex: 1 },
      { kind: "tool_use_start", blockIndex: 2, id: "tu_x", name: "FileRead" },
      { kind: "tool_use_input_delta", blockIndex: 2, partial: "{}" },
      { kind: "block_stop", blockIndex: 2 },
      {
        kind: "message_end",
        stopReason: "tool_use",
        usage: { inputTokens: 1, outputTokens: 1 },
      },
    ]);
    const out = await readOne(
      { llm, model: "test", sse, onError: () => {} },
      "",
      [],
      [],
    );
    expect(out.blocks.map((b) => b.type)).toEqual([
      "thinking",
      "text",
      "tool_use",
    ]);
  });

  it("passes system + tools into the underlying stream call", async () => {
    const seen: LLMStreamRequest[] = [];
    const sse = new FakeSse();
    const llm = {
      async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
        seen.push(req);
        yield {
          kind: "message_end",
          stopReason: "end_turn",
          usage: { inputTokens: 1, outputTokens: 1 },
        };
      },
    } as unknown as LLMClient;
    await readOne(
      { llm, model: "opus-4-7", sse, onError: () => {} },
      "SYS",
      [{ role: "user", content: "hi" }],
      [
        {
          name: "Echo",
          description: "",
          input_schema: { type: "object", properties: {} },
        },
      ],
    );
    expect(seen.length).toBe(1);
    expect(seen[0]?.system).toBe("SYS");
    expect(seen[0]?.model).toBe("opus-4-7");
    expect(seen[0]?.tools?.length).toBe(1);
  });

  it("omits tools field when toolDefs is empty", async () => {
    const seen: LLMStreamRequest[] = [];
    const sse = new FakeSse();
    const llm = {
      async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
        seen.push(req);
        yield {
          kind: "message_end",
          stopReason: "end_turn",
          usage: { inputTokens: 1, outputTokens: 1 },
        };
      },
    } as unknown as LLMClient;
    await readOne(
      { llm, model: "test", sse, onError: () => {} },
      "",
      [],
      [],
    );
    expect(seen[0]?.tools).toBeUndefined();
  });

  it("aborts stream on repetitive text and forces end_turn", async () => {
    const sse = new FakeSse();
    // Simulate degenerate LLM output: same sentence repeated many times.
    const repeated = "사장님, KB에 직접 파일 업로드 기능이 없어요. document-reader 스킬로 업로드하는 것 같습니다. 확인하겠습니다.";
    const events: LLMEvent[] = [];
    // Feed the repeated sentence as many text_delta events (simulating
    // a model that degenerates within a single response).
    for (let i = 0; i < 10; i++) {
      events.push({ kind: "text_delta", blockIndex: 0, delta: repeated });
    }
    events.push({
      kind: "message_end",
      stopReason: "end_turn",
      usage: { inputTokens: 100, outputTokens: 500 },
    });
    const llm = makeLLM(events);
    const out = await readOne(
      { llm, model: "test", sse, onError: () => {} },
      "",
      [],
      [],
    );
    // Should have aborted early — not all 10 deltas consumed.
    expect(out.stopReason).toBe("end_turn");
    expect(out.blocks.length).toBe(1);
    expect(out.blocks[0]?.type).toBe("text");
    // The last SSE event should be the repetition warning.
    const lastAgentEvent = sse.events[sse.events.length - 1] as { type: string; delta: string };
    expect(lastAgentEvent?.delta).toContain("반복 감지됨");
  });
});
