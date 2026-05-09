/**
 * Tests for Session mid-turn injection queue (#86).
 * Pure state-machine tests — no Agent construction needed.
 */

import { describe, it, expect, vi } from "vitest";
import { Session } from "./Session.js";
import type { Agent } from "./Agent.js";
import type { SessionMeta } from "./Session.js";

function makeSession(): Session {
  const meta: SessionMeta = {
    sessionKey: "agent:main:app:default:test",
    botId: "test-bot",
    channel: { type: "app", channelId: "default" },
    createdAt: Date.now(),
    lastActivityAt: Date.now(),
  };
  // Minimal Agent stub — Session ctor doesn't touch most fields until
  // runTurn. Casting through unknown keeps the field list private.
  const agent = {
    config: {},
    sessionsDir: "/tmp/test",
  } as unknown as Agent;
  return new Session(meta, agent);
}

describe("Session.injectMessage", () => {
  it("queues a message and returns injectionId + queuedCount", () => {
    const s = makeSession();
    const result = s.injectMessage("hello", "web");
    expect(result).not.toBeNull();
    expect(result!.injectionId).toMatch(/^inj-.*-1$/);
    expect(result!.queuedCount).toBe(1);
    expect(s.hasPendingInjections()).toBe(true);
    expect(s.peekPendingInjectionCount()).toBe(1);
  });

  it("asks the active turn to resume the current LLM step after queueing", () => {
    const s = makeSession();
    const requestSteerResume = vi.fn(() => ({ status: "accepted" as const }));
    (s as unknown as {
      activeTurn: { requestSteerResume: typeof requestSteerResume };
    }).activeTurn = { requestSteerResume };

    const result = s.injectMessage("steer now", "web");

    expect(result).not.toBeNull();
    expect(requestSteerResume).toHaveBeenCalledWith("web");
  });

  it("increments injection sequence across multiple injects", () => {
    const s = makeSession();
    const first = s.injectMessage("a", "web")!;
    const second = s.injectMessage("b", "mobile")!;
    expect(first.injectionId).toMatch(/-1$/);
    expect(second.injectionId).toMatch(/-2$/);
    expect(s.peekPendingInjectionCount()).toBe(2);
  });

  it("rejects the 6th injection (caps at MAX_PENDING_INJECTIONS)", () => {
    const s = makeSession();
    for (let i = 0; i < Session.MAX_PENDING_INJECTIONS; i++) {
      expect(s.injectMessage(`msg-${i}`)).not.toBeNull();
    }
    const overflow = s.injectMessage("overflow");
    expect(overflow).toBeNull();
    expect(s.peekPendingInjectionCount()).toBe(Session.MAX_PENDING_INJECTIONS);
  });

  it("drainPendingInjections returns queued messages and empties the queue", () => {
    const s = makeSession();
    s.injectMessage("one", "web");
    s.injectMessage("two", "mobile");
    const drained = s.drainPendingInjections();
    expect(drained).toHaveLength(2);
    expect(drained[0]?.text).toBe("one");
    expect(drained[1]?.text).toBe("two");
    expect(drained[0]?.metadata?.injection?.source).toBe("web");
    expect(drained[1]?.metadata?.injection?.source).toBe("mobile");
    expect(s.hasPendingInjections()).toBe(false);
    expect(s.peekPendingInjectionCount()).toBe(0);
  });

  it("drain is idempotent on empty queue", () => {
    const s = makeSession();
    expect(s.drainPendingInjections()).toEqual([]);
    expect(s.drainPendingInjections()).toEqual([]);
    expect(s.hasPendingInjections()).toBe(false);
  });

  it("after drain, new injections restart after the prior sequence", () => {
    const s = makeSession();
    s.injectMessage("pre-drain-1")!;
    s.injectMessage("pre-drain-2")!;
    s.drainPendingInjections();
    const next = s.injectMessage("post-drain")!;
    expect(next.injectionId).toMatch(/-3$/); // seq keeps counting
    expect(next.queuedCount).toBe(1);
  });

  it("defaults source to 'api' when omitted", () => {
    const s = makeSession();
    s.injectMessage("implicit");
    const drained = s.drainPendingInjections();
    expect(drained[0]?.metadata?.injection?.source).toBe("api");
  });
});
