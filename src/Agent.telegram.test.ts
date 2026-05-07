/**
 * Agent × Telegram wiring — end-to-end behaviour test with a stubbed
 * poller factory + stubbed LLM client. No network calls.
 *
 * What we're verifying (C1 acceptance criteria):
 *   1. `Agent` extended with `telegramBotToken` + a poller factory
 *      instantiates a poller and registers an inbound handler.
 *   2. Firing a canned InboundMessage through the handler causes
 *      Session.runTurn to run — and that session's meta.channel is
 *      `{type:"telegram", channelId:<chatId>}` (Invariant A).
 *   3. After the turn ends, the adapter's `send()` is called with the
 *      accumulated assistant text + reply threading.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Agent } from "./Agent.js";
import type { LLMClient, LLMEvent, LLMStreamRequest } from "./transport/LLMClient.js";
import type {
  ChannelAdapter,
  InboundHandler,
  InboundMessage,
  OutboundMessage,
} from "./channels/ChannelAdapter.js";

class StubLLM {
  async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    void req;
    yield { kind: "text_delta", blockIndex: 0, delta: "Hello" };
    yield { kind: "text_delta", blockIndex: 0, delta: " world" };
    yield {
      kind: "message_end",
      stopReason: "end_turn",
      usage: { inputTokens: 1, outputTokens: 1 },
    };
  }
}

class FakeTelegramAdapter implements ChannelAdapter {
  readonly kind = "telegram" as const;
  handler: InboundHandler | null = null;
  sent: OutboundMessage[] = [];
  typingChatIds: string[] = [];
  started = false;
  stopped = false;

  async start(): Promise<void> {
    this.started = true;
  }
  async stop(): Promise<void> {
    this.stopped = true;
  }
  onInboundMessage(h: InboundHandler): void {
    this.handler = h;
  }
  async send(msg: OutboundMessage): Promise<void> {
    this.sent.push(msg);
  }
  async sendDocument() {
    return { provider: "telegram" as const, channelId: "chat-1" };
  }
  async sendPhoto() {
    return { provider: "telegram" as const, channelId: "chat-1" };
  }
  async sendTyping(chatId: string): Promise<void> {
    this.typingChatIds.push(chatId);
  }
}

describe("Agent × Telegram adapter wiring", () => {
  let workspaceRoot: string;
  let adapter: FakeTelegramAdapter;
  let agent: Agent;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "agent-tg-"));
    adapter = new FakeTelegramAdapter();
    agent = new Agent({
      botId: "bot-1",
      userId: "user-1",
      workspaceRoot,
      gatewayToken: "tok",
      apiProxyUrl: "http://proxy",
      chatProxyUrl: "http://chat",
      redisUrl: "redis://r",
      model: "claude-haiku",
      telegramBotToken: "test-token",
      telegramAdapterFactory: () => adapter,
    });
    // Override the LLMClient with the stub. LLMClient is instantiated
    // in the Agent ctor so this mutates after the fact.
    (agent as unknown as { llm: LLMClient }).llm = new StubLLM() as unknown as LLMClient;
    await agent.start();
  });

  afterEach(async () => {
    await agent.stop();
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("wires the adapter at start() and registers an inbound handler", () => {
    expect(adapter.started).toBe(true);
    expect(typeof adapter.handler).toBe("function");
  });

  it("inbound message → runTurn with channel=telegram + send() reply", async () => {
    const inbound: InboundMessage = {
      channel: "telegram",
      chatId: "777",
      userId: "777",
      text: "ping",
      messageId: "mid-42",
      raw: {},
    };
    await adapter.handler!(inbound);

    // Find the session created for this chat.
    const sessions = agent.listSessions();
    const target = sessions.find(
      (s) => s.meta.channel.type === "telegram" && s.meta.channel.channelId === "777",
    );
    expect(target).toBeDefined();

    // Reply should have been dispatched with the accumulated text.
    expect(adapter.sent).toHaveLength(1);
    expect(adapter.sent[0]!.chatId).toBe("777");
    expect(adapter.sent[0]!.text).toBe("Hello world");
    expect(adapter.sent[0]!.replyToMessageId).toBe("mid-42");
  });

  it("fires sendTyping on the adapter when the inbound turn begins", async () => {
    const inbound: InboundMessage = {
      channel: "telegram",
      chatId: "555",
      userId: "555",
      text: "ping",
      messageId: "mid-1",
      raw: {},
    };
    await adapter.handler!(inbound);
    // At least one immediate sendTyping for this chat must have landed
    // before the turn completed. Extra ticks (every 4s) are OK but not
    // required given the synchronous StubLLM.
    expect(adapter.typingChatIds.length).toBeGreaterThanOrEqual(1);
    expect(adapter.typingChatIds[0]).toBe("555");
  });

  it("stop() propagates to the adapter", async () => {
    await agent.stop();
    expect(adapter.stopped).toBe(true);
  });
});
