/**
 * Builtin slash-command handler tests: /compact, /reset, /status.
 *
 * Drives commands through a real Agent + Session (with a stub LLM) so
 * the wiring in Agent.ctor + Session.runTurn is exercised end-to-end.
 * The Haiku summariser is intercepted at the LLMClient.stream boundary
 * so no network call is made.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Agent } from "../Agent.js";
import type {
  LLMClient,
  LLMEvent,
  LLMStreamRequest,
} from "../transport/LLMClient.js";
import { CaptureSseWriter } from "../channels/ChannelDispatcher.js";
import { buildSessionKey } from "../channels/ChannelDispatcher.js";
import type { InboundMessage } from "../channels/ChannelAdapter.js";
import {
  applyResetToSessionKey,
  ResetCounterStore,
} from "./resetCounters.js";

/**
 * LLM stub: summariser emits a canned handoff summary; any other
 * model (shouldn't happen in these tests) also emits a short reply so
 * the test never hangs.
 */
class StubLLM {
  calls: LLMStreamRequest[] = [];
  async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    this.calls.push(req);
    yield { kind: "text_delta", blockIndex: 0, delta: "COMPACTED_SUMMARY_OK" };
    yield {
      kind: "message_end",
      stopReason: "end_turn",
      usage: { inputTokens: 1, outputTokens: 1 },
    };
  }
}

function inbound(text: string, chatId = "777"): InboundMessage {
  return {
    channel: "telegram",
    chatId,
    userId: chatId,
    text,
    messageId: "mid-1",
    raw: {},
  };
}

describe("Builtin slash commands", () => {
  let workspaceRoot: string;
  let agent: Agent;
  let stubLLM: StubLLM;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "slash-"));
    agent = new Agent({
      botId: "bot-slash",
      userId: "user-slash",
      workspaceRoot,
      gatewayToken: "tok",
      apiProxyUrl: "http://proxy",
      chatProxyUrl: "http://chat",
      redisUrl: "redis://r",
      model: "claude-haiku",
    });
    stubLLM = new StubLLM();
    (agent as unknown as { llm: LLMClient }).llm =
      stubLLM as unknown as LLMClient;
    // contextEngine is constructed with the OLD llm reference. Re-wire
    // it explicitly so /compact's summariser uses the stub too.
    (
      agent as unknown as {
        contextEngine: { llm: LLMClient };
      }
    ).contextEngine.llm = stubLLM as unknown as LLMClient;
    await agent.start();
  });

  afterEach(async () => {
    await agent.stop();
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("/compact writes a compaction boundary to the transcript", async () => {
    const channel = { type: "telegram" as const, channelId: "777" };
    const sessionKey = buildSessionKey(inbound("ignored", "777"));
    const session = await agent.getOrCreateSession(sessionKey, channel);
    // Seed a non-empty transcript so maybeCompact has something to
    // summarise. Otherwise the engine would still write a boundary at
    // tokenLimit=0, but this keeps the test closer to real usage.
    await session.transcript.append({
      kind: "user_message",
      ts: Date.now(),
      turnId: "seed",
      text: "hello world",
    });
    const capture = new CaptureSseWriter();
    await session.runTurn(
      { text: "/compact", receivedAt: Date.now() },
      capture,
    );
    const entries = await session.transcript.readAll();
    const boundaries = entries.filter((e) => e.kind === "compaction_boundary");
    expect(boundaries).toHaveLength(1);
    // The SSE writer's accumulated text should contain the success
    // notice from makeCompactCommand.
    expect(capture.finalText()).toContain("compacted");
    // Turn registered as committed without running the real LLM loop.
    expect(capture.turnStatus()).toBe("committed");
  });

  it("/compact does not consume the session budget", async () => {
    const channel = { type: "telegram" as const, channelId: "777" };
    const session = await agent.getOrCreateSession(
      buildSessionKey(inbound("ignored", "777")),
      channel,
    );
    const before = session.budgetStats();
    await session.runTurn(
      { text: "/compact", receivedAt: Date.now() },
      new CaptureSseWriter(),
    );
    const after = session.budgetStats();
    expect(after.turns).toBe(before.turns);
    expect(after.inputTokens).toBe(before.inputTokens);
    expect(after.outputTokens).toBe(before.outputTokens);
  });

  it("/reset bumps the per-channel counter + persists to disk", async () => {
    const channel = { type: "telegram" as const, channelId: "777" };
    const session = await agent.getOrCreateSession(
      buildSessionKey(inbound("ignored", "777")),
      channel,
    );
    expect(await agent.resetCounters.get(channel)).toBe(0);
    const capture = new CaptureSseWriter();
    await session.runTurn(
      { text: "/reset", receivedAt: Date.now() },
      capture,
    );
    expect(capture.finalText()).toContain("Conversation reset");
    expect(await agent.resetCounters.get(channel)).toBe(1);
    // Persisted — a fresh ResetCounterStore pointing at the same file
    // must report the same counter value.
    const sessionsDir = path.join(workspaceRoot, "core-agent", "sessions");
    const reload = new ResetCounterStore(sessionsDir);
    expect(await reload.get(channel)).toBe(1);
  });

  it("/reset → next inbound lands in a fresh sessionKey namespace", async () => {
    const channel = { type: "telegram" as const, channelId: "777" };
    const baseKey = buildSessionKey(inbound("ignored", "777"));
    const sessionA = await agent.getOrCreateSession(baseKey, channel);
    await sessionA.runTurn(
      { text: "/reset", receivedAt: Date.now() },
      new CaptureSseWriter(),
    );
    const counter = await agent.resetCounters.get(channel);
    const nextKey = applyResetToSessionKey(baseKey, counter);
    expect(nextKey).not.toBe(baseKey);
    const sessionB = await agent.getOrCreateSession(nextKey, channel);
    // Fresh Session instance — not the same object.
    expect(sessionB).not.toBe(sessionA);
  });

  it("/status prints the expected fields", async () => {
    const channel = { type: "telegram" as const, channelId: "777" };
    const session = await agent.getOrCreateSession(
      buildSessionKey(inbound("ignored", "777")),
      channel,
    );
    const capture = new CaptureSseWriter();
    await session.runTurn(
      { text: "/status", receivedAt: Date.now() },
      capture,
    );
    const out = capture.finalText();
    expect(out).toContain("Session status");
    expect(out).toContain("Role:");
    expect(out).toContain("Channel: telegram:777");
    expect(out).toContain("Reset counter: 0");
    expect(out).toContain("Turns (this session):");
    expect(out).toContain("Tokens —");
    expect(out).toContain("Skills loaded:");
    expect(out).toContain("Active crons:");
    expect(out).toContain("Discipline:");
    expect(out).not.toContain("Model: claude-haiku");

    const overrideCapture = new CaptureSseWriter();
    await session.runTurn(
      { text: "/status", receivedAt: Date.now() },
      overrideCapture,
      { runtimeModelOverride: "claude-sonnet-4-5" },
    );
    expect(overrideCapture.finalText()).toContain("Model: claude-sonnet-4-5");
  });

  it("unknown /foo falls through to the normal Turn path", async () => {
    const channel = { type: "telegram" as const, channelId: "777" };
    const session = await agent.getOrCreateSession(
      buildSessionKey(inbound("ignored", "777")),
      channel,
    );
    const capture = new CaptureSseWriter();
    await session.runTurn(
      { text: "/totally-made-up-command", receivedAt: Date.now() },
      capture,
    );
    // If it fell through, the stub LLM was invoked (Turn ran).
    expect(stubLLM.calls.length).toBeGreaterThan(0);
  });

  it("whitespace around a command is tolerated", async () => {
    const channel = { type: "telegram" as const, channelId: "777" };
    const session = await agent.getOrCreateSession(
      buildSessionKey(inbound("ignored", "777")),
      channel,
    );
    const capture = new CaptureSseWriter();
    await session.runTurn(
      { text: "  /status  ", receivedAt: Date.now() },
      capture,
    );
    expect(capture.finalText()).toContain("Session status");
  });

  it("/compacting (non-matching prefix) does NOT hit the slash path", async () => {
    const channel = { type: "telegram" as const, channelId: "777" };
    const session = await agent.getOrCreateSession(
      buildSessionKey(inbound("ignored", "777")),
      channel,
    );
    const capture = new CaptureSseWriter();
    await session.runTurn(
      { text: "/compacting the file", receivedAt: Date.now() },
      capture,
    );
    // Non-slash fallthrough ⇒ LLM invoked. No compaction boundary.
    expect(stubLLM.calls.length).toBeGreaterThan(0);
    const entries = await session.transcript.readAll();
    expect(entries.filter((e) => e.kind === "compaction_boundary")).toHaveLength(
      0,
    );
  });
});
