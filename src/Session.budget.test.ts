/**
 * T1-06 — Session budget + costUsd unit tests.
 *
 * Covers:
 *   - computeUsd math for a known model
 *   - computeUsd unknown-model fallback (returns 0)
 *   - Session accumulates over multiple turns
 *   - Budget exceeded by turns (reason=turns)
 *   - Budget exceeded by cost (reason=cost)
 *   - Under-budget happy path
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  Session,
  DEFAULT_MAX_TURNS_PER_SESSION,
  DEFAULT_MAX_COST_USD_PER_SESSION,
  type SessionMeta,
} from "./Session.js";
import type { Agent, AgentConfig } from "./Agent.js";
import type { TokenUsage } from "./util/types.js";
import {
  computeUsd,
  MODEL_CAPABILITIES,
} from "./llm/modelCapabilities.js";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

function makeAgent(overrides: Partial<AgentConfig> = {}): Agent {
  const config: AgentConfig = {
    botId: "bot-test",
    userId: "user-test",
    workspaceRoot: "/tmp/core-agent-test-budget",
    gatewayToken: "test",
    apiProxyUrl: "http://localhost",
    chatProxyUrl: "http://localhost",
    redisUrl: "redis://localhost",
    model: "claude-opus-4-7",
    ...overrides,
  };
  // Minimal stub — Session only reads `.config` + `.sessionsDir` at
  // construction time (for Transcript), so we can avoid instantiating
  // the real Agent (which spins up ToolRegistry + LLMClient + hooks).
  const stub = {
    config,
    sessionsDir: "/tmp/core-agent-test-budget/sessions",
  } as unknown as Agent;
  return stub;
}

function makeSession(agent: Agent): Session {
  const now = Date.now();
  const meta: SessionMeta = {
    sessionKey: "agent:main:app:general:1",
    botId: agent.config.botId,
    channel: { type: "app", channelId: "general" },
    createdAt: now,
    lastActivityAt: now,
  };
  return new Session(meta, agent);
}

function usage(
  inputTokens: number,
  outputTokens: number,
  costUsd: number,
): TokenUsage {
  return { inputTokens, outputTokens, costUsd };
}

describe("computeUsd", () => {
  it("computes USD correctly for a known model (Opus 4.7: $15 in / $75 out per Mtok)", () => {
    // 1,000,000 input tokens × $15 = $15
    // 1,000,000 output tokens × $75 = $75
    // Total = $90
    expect(computeUsd("claude-opus-4-7", 1_000_000, 1_000_000)).toBeCloseTo(
      90,
      6,
    );
    // Smaller realistic numbers: 12k in + 3k out
    // = (12000/1e6)*15 + (3000/1e6)*75 = 0.18 + 0.225 = 0.405
    expect(computeUsd("claude-opus-4-7", 12_000, 3_000)).toBeCloseTo(0.405, 6);
  });

  it("returns 0 for unknown model (fail-open)", () => {
    expect(computeUsd("claude-mystery-9", 1_000_000, 1_000_000)).toBe(0);
    expect(computeUsd("", 100, 100)).toBe(0);
  });

  it("capability registry contains the expected model ids", () => {
    expect(MODEL_CAPABILITIES["claude-opus-4-6"]).toBeDefined();
    expect(MODEL_CAPABILITIES["claude-opus-4-7"]).toBeDefined();
    expect(MODEL_CAPABILITIES["claude-sonnet-4-6"]).toBeDefined();
    expect(MODEL_CAPABILITIES["claude-haiku-4-5-20251001"]).toBeDefined();
  });
});

describe("Session budget", () => {
  it("accumulates usage across multiple turns", () => {
    const session = makeSession(makeAgent());
    session.recordTurnUsage(usage(1_000, 500, 0.05));
    session.recordTurnUsage(usage(2_000, 800, 0.12));
    const stats = session.budgetStats();
    expect(stats.turns).toBe(2);
    expect(stats.inputTokens).toBe(3_000);
    expect(stats.outputTokens).toBe(1_300);
    expect(stats.costUsd).toBeCloseTo(0.17, 6);
  });

  it("exceeded=true with reason=turns when cumulativeTurns reaches maxTurns", () => {
    const session = makeSession(makeAgent({ maxTurnsPerSession: 3 }));
    for (let i = 0; i < 3; i++) {
      session.recordTurnUsage(usage(10, 10, 0.0001));
    }
    const result = session.budgetExceeded();
    expect(result.exceeded).toBe(true);
    expect(result.reason).toBe("turns");
  });

  it("exceeded=true with reason=cost when cumulativeCostUsd reaches maxCostUsd", () => {
    // maxTurns high so turns don't trip first. maxCost=1 USD, turn=0.6 × 2.
    const session = makeSession(
      makeAgent({ maxTurnsPerSession: 100, maxCostUsdPerSession: 1 }),
    );
    session.recordTurnUsage(usage(100, 100, 0.6));
    expect(session.budgetExceeded().exceeded).toBe(false);
    session.recordTurnUsage(usage(100, 100, 0.6));
    const result = session.budgetExceeded();
    expect(result.exceeded).toBe(true);
    expect(result.reason).toBe("cost");
  });

  it("under budget → exceeded=false, no reason", () => {
    const session = makeSession(makeAgent());
    session.recordTurnUsage(usage(1_000, 500, 0.01));
    const result = session.budgetExceeded();
    expect(result.exceeded).toBe(false);
    expect(result.reason).toBeUndefined();
  });

  it("defaults from AgentConfig when unset (50 turns, $10)", () => {
    const session = makeSession(makeAgent());
    expect(session.maxTurns).toBe(DEFAULT_MAX_TURNS_PER_SESSION);
    expect(session.maxCostUsd).toBe(DEFAULT_MAX_COST_USD_PER_SESSION);
  });

  it("uses AgentConfig overrides when provided", () => {
    const session = makeSession(
      makeAgent({ maxTurnsPerSession: 7, maxCostUsdPerSession: 3.5 }),
    );
    expect(session.maxTurns).toBe(7);
    expect(session.maxCostUsd).toBe(3.5);
  });
});

describe("Session.hydrateBudgetFromTranscript", () => {
  let tmpDir: string;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "budget-hydrate-"));
    await fs.mkdir(path.join(tmpDir, "sessions"), { recursive: true });
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  function makeSessionWithDir(dir: string): Session {
    const agent = {
      config: {
        botId: "bot-test",
        userId: "user-test",
        workspaceRoot: dir,
        gatewayToken: "test",
        apiProxyUrl: "http://localhost",
        chatProxyUrl: "http://localhost",
        redisUrl: "redis://localhost",
        model: "claude-opus-4-7",
      },
      sessionsDir: path.join(dir, "sessions"),
    } as unknown as Agent;
    const now = Date.now();
    return new Session(
      {
        sessionKey: "agent:main:app:general:1",
        botId: "bot-test",
        channel: { type: "app", channelId: "general" },
        createdAt: now,
        lastActivityAt: now,
      },
      agent,
    );
  }

  it("hydrates cumulativeTurns from transcript turn_committed entries", async () => {
    const session = makeSessionWithDir(tmpDir);
    // Write transcript entries directly to disk to simulate a prior pod
    const entries = [
      { kind: "user_message", ts: 1, turnId: "t1", text: "hello" },
      { kind: "assistant_text", ts: 2, turnId: "t1", text: "hi" },
      { kind: "turn_committed", ts: 3, turnId: "t1", inputTokens: 100, outputTokens: 50 },
      { kind: "user_message", ts: 4, turnId: "t2", text: "next" },
      { kind: "assistant_text", ts: 5, turnId: "t2", text: "ok" },
      { kind: "turn_committed", ts: 6, turnId: "t2", inputTokens: 200, outputTokens: 80 },
      { kind: "user_message", ts: 7, turnId: "t3", text: "more" },
      { kind: "assistant_text", ts: 8, turnId: "t3", text: "sure" },
      { kind: "turn_committed", ts: 9, turnId: "t3", inputTokens: 150, outputTokens: 60 },
    ];
    const content = entries.map((e) => JSON.stringify(e)).join("\n") + "\n";
    await fs.writeFile(session.transcript.filePath, content);

    expect(session.budgetStats().turns).toBe(0);
    await session.hydrateBudgetFromTranscript();
    expect(session.budgetStats().turns).toBe(3);
    expect(session.budgetStats().inputTokens).toBe(450);
    expect(session.budgetStats().outputTokens).toBe(190);
  });

  it("is idempotent — skips if turns already > 0", async () => {
    const session = makeSessionWithDir(tmpDir);
    session.recordTurnUsage(usage(10, 10, 0.001));
    // Even with transcript on disk, hydrate should skip
    const entries = [
      { kind: "turn_committed", ts: 1, turnId: "t1", inputTokens: 9999, outputTokens: 9999 },
    ];
    await fs.writeFile(
      session.transcript.filePath,
      entries.map((e) => JSON.stringify(e)).join("\n") + "\n",
    );
    await session.hydrateBudgetFromTranscript();
    expect(session.budgetStats().turns).toBe(1); // not 1+1
    expect(session.budgetStats().inputTokens).toBe(10); // not 9999
  });

  it("handles empty/missing transcript gracefully (stays at 0)", async () => {
    const session = makeSessionWithDir(tmpDir);
    await session.hydrateBudgetFromTranscript();
    expect(session.budgetStats().turns).toBe(0);
  });

  it("hydrates meta.createdAt from earliest transcript entry ts", async () => {
    const session = makeSessionWithDir(tmpDir);
    const originalTs = new Date("2026-04-20T10:00:00Z").getTime();
    // Session was created with Date.now() (simulating pod restart)
    expect(session.meta.createdAt).toBeGreaterThan(originalTs);

    const entries = [
      { kind: "turn_started", ts: originalTs, turnId: "t1", declaredRoute: "direct" },
      { kind: "user_message", ts: originalTs + 100, turnId: "t1", text: "hello" },
      { kind: "assistant_text", ts: originalTs + 200, turnId: "t1", text: "hi" },
      { kind: "turn_committed", ts: originalTs + 300, turnId: "t1", inputTokens: 100, outputTokens: 50 },
    ];
    await fs.writeFile(
      session.transcript.filePath,
      entries.map((e) => JSON.stringify(e)).join("\n") + "\n",
    );

    await session.hydrateBudgetFromTranscript();
    // createdAt should now be the earliest entry's ts
    expect(session.meta.createdAt).toBe(originalTs);
  });

  it("does not overwrite createdAt when transcript is empty", async () => {
    const session = makeSessionWithDir(tmpDir);
    const before = session.meta.createdAt;
    await session.hydrateBudgetFromTranscript();
    expect(session.meta.createdAt).toBe(before);
  });

  it("hydrates createdAt from aborted-only transcript", async () => {
    const session = makeSessionWithDir(tmpDir);
    const originalTs = new Date("2026-04-19T08:00:00Z").getTime();

    const entries = [
      { kind: "turn_started", ts: originalTs, turnId: "t1", declaredRoute: "direct" },
      { kind: "user_message", ts: originalTs + 100, turnId: "t1", text: "msg" },
      { kind: "assistant_text", ts: originalTs + 200, turnId: "t1", text: "reply" },
      { kind: "turn_aborted", ts: originalTs + 300, turnId: "t1", reason: "hook blocked" },
    ];
    await fs.writeFile(
      session.transcript.filePath,
      entries.map((e) => JSON.stringify(e)).join("\n") + "\n",
    );

    await session.hydrateBudgetFromTranscript();
    expect(session.meta.createdAt).toBe(originalTs);
  });
});
