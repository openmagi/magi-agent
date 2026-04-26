/**
 * repeatedFailureGuard unit tests.
 *
 * Uses the real filesystem in an OS tmp dir for the state file so the
 * atomic-write + read round-trip is exercised end-to-end.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  makeRepeatedFailureGuardHook,
  recordFailure,
  readCircuitState,
  signatureFor,
  findActiveTrip,
  CIRCUIT_THRESHOLD,
  CIRCUIT_COOLDOWN_MS,
  CIRCUIT_WINDOW_MS,
  __testing,
} from "./repeatedFailureGuard.js";
import type { HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import type { AgentEvent } from "../../transport/SseWriter.js";
import type { LLMMessage, LLMToolDef } from "../../transport/LLMClient.js";

function makeHookCtx(): {
  ctx: HookContext;
  emitted: AgentEvent[];
  logs: Array<{ level: string; msg: string }>;
} {
  const emitted: AgentEvent[] = [];
  const logs: Array<{ level: string; msg: string }> = [];
  const llm = {
    stream: async function* () {
      yield {
        kind: "message_end",
        stopReason: "end_turn",
        usage: { inputTokens: 0, outputTokens: 0 },
      };
    },
  } as unknown as LLMClient;
  const ctx: HookContext = {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-1",
    llm,
    transcript: [],
    emit: (e) => emitted.push(e),
    log: (level, msg) => logs.push({ level, msg }),
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
  return { ctx, emitted, logs };
}

async function mkTemp(): Promise<string> {
  return await fs.mkdtemp(path.join(os.tmpdir(), "circuit-breaker-test-"));
}

let tmp: string;
beforeEach(async () => {
  tmp = await mkTemp();
});
afterEach(async () => {
  await fs.rm(tmp, { recursive: true, force: true });
});

describe("signatureFor", () => {
  it("is order-independent over path array", () => {
    const a = signatureFor("h", ["b.md", "a.md"]);
    const b = signatureFor("h", ["a.md", "b.md"]);
    expect(a).toBe(b);
  });
  it("differs when hookName differs", () => {
    expect(signatureFor("h1", ["x"])).not.toBe(signatureFor("h2", ["x"]));
  });
});

describe("recordFailure", () => {
  it("creates a fresh entry on first call", async () => {
    const now = 1_000;
    const sig = signatureFor("builtin:sealed-files", ["A.md"]);
    const { entry, tripped } = await recordFailure(
      { workspaceRoot: tmp, now: () => now },
      sig,
    );
    expect(entry.count).toBe(1);
    expect(entry.firstAt).toBe(now);
    expect(entry.lastAt).toBe(now);
    expect(entry.trippedUntil).toBeUndefined();
    expect(tripped).toBe(false);

    const state = await readCircuitState(tmp);
    expect(state[sig]?.count).toBe(1);
  });

  it("trips at the threshold within the rolling window", async () => {
    const sig = signatureFor("builtin:sealed-files", ["equity-research.md"]);
    let now = 1_000;
    let tripped = false;
    for (let i = 0; i < CIRCUIT_THRESHOLD; i++) {
      const rec = await recordFailure(
        { workspaceRoot: tmp, now: () => now },
        sig,
      );
      tripped = rec.tripped;
      now += 10_000;
    }
    expect(tripped).toBe(true);

    const state = await readCircuitState(tmp);
    const entry = state[sig];
    expect(entry).toBeDefined();
    expect(entry?.count).toBeGreaterThanOrEqual(CIRCUIT_THRESHOLD);
    expect(entry?.trippedUntil).toBeDefined();
    // trippedUntil ≈ last now + COOLDOWN.
    const lastTick = 1_000 + (CIRCUIT_THRESHOLD - 1) * 10_000;
    expect(entry?.trippedUntil).toBe(lastTick + CIRCUIT_COOLDOWN_MS);
  });

  it("resets the window when the prior firstAt is stale", async () => {
    const sig = signatureFor("builtin:sealed-files", ["A.md"]);
    await recordFailure({ workspaceRoot: tmp, now: () => 1_000 }, sig);
    await recordFailure({ workspaceRoot: tmp, now: () => 1_000 + 1_000 }, sig);
    // Advance past window.
    const later = 1_000 + CIRCUIT_WINDOW_MS + 1;
    const rec = await recordFailure(
      { workspaceRoot: tmp, now: () => later },
      sig,
    );
    expect(rec.entry.count).toBe(1);
    expect(rec.entry.firstAt).toBe(later);
    expect(rec.tripped).toBe(false);
  });
});

describe("findActiveTrip", () => {
  it("returns null when nothing is tripped", () => {
    expect(findActiveTrip({}, 1_000)).toBeNull();
    expect(
      findActiveTrip(
        { a: { count: 1, firstAt: 0, lastAt: 0 } },
        1_000,
      ),
    ).toBeNull();
  });
  it("returns the trip with the largest remaining window", () => {
    const now = 1_000_000;
    const got = findActiveTrip(
      {
        sig1: { count: 3, firstAt: 0, lastAt: 0, trippedUntil: now + 60_000 },
        sig2: { count: 3, firstAt: 0, lastAt: 0, trippedUntil: now + 600_000 },
        sig3: { count: 3, firstAt: 0, lastAt: 0, trippedUntil: now - 1 }, // expired
      },
      now,
    );
    expect(got?.signature).toBe("sig2");
  });
});

describe("beforeLLMCall hook", () => {
  function makeArgs(): {
    messages: LLMMessage[];
    tools: LLMToolDef[];
    system: string;
    iteration: number;
  } {
    return { messages: [], tools: [], system: "", iteration: 0 };
  }

  it("allows through when no state file exists", async () => {
    const hook = makeRepeatedFailureGuardHook({
      workspaceRoot: tmp,
      now: () => 1_000,
    });
    const { ctx } = makeHookCtx();
    const out = await hook.handler(makeArgs(), ctx);
    expect(out).toBeDefined();
    if (!out) throw new Error("hook returned void");
    expect(out.action).toBe("continue");
  });

  it("3 sequential sealed_files violations trip the breaker, next LLM call blocked until expiry", async () => {
    // Simulate 3 failures in quick succession.
    const sig = signatureFor("builtin:sealed-files", [
      "skills/equity-research/SKILL.md",
    ]);
    for (let i = 0; i < CIRCUIT_THRESHOLD; i++) {
      await recordFailure(
        { workspaceRoot: tmp, now: () => 1_000 + i * 1_000 },
        sig,
      );
    }
    // Sanity check — entry is tripped.
    const state = await readCircuitState(tmp);
    expect(state[sig]?.trippedUntil).toBeDefined();

    // Next beforeLLMCall should block.
    const trippedNow = 1_000 + (CIRCUIT_THRESHOLD - 1) * 1_000 + 1_000;
    const hook = makeRepeatedFailureGuardHook({
      workspaceRoot: tmp,
      now: () => trippedNow,
    });
    const { ctx } = makeHookCtx();
    const out = await hook.handler(makeArgs(), ctx);
    if (!out) throw new Error("hook returned void");
    expect(out.action).toBe("block");
    if (out.action === "block") {
      expect(out.reason).toContain("Circuit breaker active");
      expect(out.reason).toContain("cooldown");
    }
  });

  it("allows through once the cooldown elapses", async () => {
    const sig = signatureFor("builtin:sealed-files", ["A.md"]);
    for (let i = 0; i < CIRCUIT_THRESHOLD; i++) {
      await recordFailure(
        { workspaceRoot: tmp, now: () => 1_000 + i * 1_000 },
        sig,
      );
    }
    const trippedAt = 1_000 + (CIRCUIT_THRESHOLD - 1) * 1_000;
    const afterExpiry = trippedAt + CIRCUIT_COOLDOWN_MS + 1;
    const hook = makeRepeatedFailureGuardHook({
      workspaceRoot: tmp,
      now: () => afterExpiry,
    });
    const { ctx } = makeHookCtx();
    const out = await hook.handler(makeArgs(), ctx);
    if (!out) throw new Error("hook returned void");
    expect(out.action).toBe("continue");
  });

  it("fail-open on corrupt state file", async () => {
    await fs.mkdir(path.dirname(__testing.statePath(tmp)), { recursive: true });
    await fs.writeFile(__testing.statePath(tmp), "{garbage", "utf8");
    const hook = makeRepeatedFailureGuardHook({
      workspaceRoot: tmp,
      now: () => 1_000,
    });
    const { ctx } = makeHookCtx();
    const out = await hook.handler(makeArgs(), ctx);
    if (!out) throw new Error("hook returned void");
    expect(out.action).toBe("continue");
  });
});
