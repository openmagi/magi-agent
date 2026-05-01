/**
 * Session.close() runtime lifecycle tests (#82).
 *
 * Covers:
 *   1. close() is idempotent (2nd call is a no-op)
 *   2. close() aborts the session's in-flight AbortController
 *   3. close() drains pendingInjections
 *   4. close() emits a `session_closed` AgentLifecycleEvent with the
 *      correct sessionKey + reason
 *   5. Agent.closeSession() returns false for unknown sessionKey
 *   6. Agent.closeSession() removes the session from the registry
 *   7. fireCron() on a non-durable record closes the synthetic
 *      session after the turn completes
 *
 * Pure state-machine tests — no HTTP / LLM / PVC side effects.
 */

import { describe, expect, it, vi } from "vitest";
import { Session, type SessionMeta } from "./Session.js";
import type { Agent, AgentLifecycleEvent } from "./Agent.js";
import type { CronRecord } from "./cron/CronScheduler.js";

interface StubAgent {
  crons: {
    records: Map<string, CronRecord>;
    get(id: string): CronRecord | null;
    delete(id: string): Promise<boolean>;
  };
  sessionsDir: string;
  config: Record<string, unknown>;
  sessions: Map<string, Session>;
  emitAgentEvent: (event: AgentLifecycleEvent) => void;
  events: AgentLifecycleEvent[];
}

function makeAgent(): StubAgent {
  const records = new Map<string, CronRecord>();
  const events: AgentLifecycleEvent[] = [];
  return {
    crons: {
      records,
      get: (id: string) => records.get(id) ?? null,
      delete: async (id: string) => records.delete(id),
    },
    sessionsDir: "/tmp/session-close-test",
    config: {},
    sessions: new Map<string, Session>(),
    events,
    emitAgentEvent(event) {
      this.events.push(event);
    },
  };
}

function makeSession(agent: StubAgent, sessionKey = "agent:main:app:x:1"): Session {
  const meta: SessionMeta = {
    sessionKey,
    botId: "bot-close-test",
    channel: { type: "app", channelId: "x" },
    createdAt: Date.now(),
    lastActivityAt: Date.now(),
  };
  return new Session(meta, agent as unknown as Agent);
}

describe("Session.close — idempotency", () => {
  it("is a no-op on the second call", async () => {
    const agent = makeAgent();
    const s = makeSession(agent);
    s.injectMessage("hello");
    await s.close("first");
    expect(s.isClosed()).toBe(true);
    expect(s.peekPendingInjectionCount()).toBe(0);
    expect(agent.events).toHaveLength(1);

    // Second call should not re-emit, not re-abort, not throw.
    await s.close("second");
    expect(agent.events).toHaveLength(1);
    expect(agent.events[0]?.type).toBe("session_closed");
    if (agent.events[0]?.type === "session_closed") {
      expect(agent.events[0].reason).toBe("first");
    }
  });
});

describe("Session.close — in-flight abort", () => {
  it("aborts the lazy AbortController when present", async () => {
    const agent = makeAgent();
    const s = makeSession(agent);
    const signal = s.getAbortSignal();
    expect(signal.aborted).toBe(false);
    const onAbort = vi.fn();
    signal.addEventListener("abort", onAbort);

    await s.close("in-flight");

    expect(signal.aborted).toBe(true);
    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it("interrupts the active turn before closing session resources", async () => {
    const agent = makeAgent();
    const s = makeSession(agent);
    const requestInterrupt = vi.fn();
    (s as unknown as { activeTurn: { requestInterrupt: typeof requestInterrupt } }).activeTurn = {
      requestInterrupt,
    };

    await s.close("shutdown");

    expect(requestInterrupt).toHaveBeenCalledWith(false, "shutdown");
  });

  it("is safe when no AbortController was ever allocated", async () => {
    const agent = makeAgent();
    const s = makeSession(agent);
    await expect(s.close()).resolves.toBeUndefined();
    expect(s.isClosed()).toBe(true);
  });
});

describe("Session.close — pendingInjections", () => {
  it("drains the queue so subsequent drain returns empty", async () => {
    const agent = makeAgent();
    const s = makeSession(agent);
    s.injectMessage("one", "web");
    s.injectMessage("two", "mobile");
    expect(s.peekPendingInjectionCount()).toBe(2);

    await s.close();

    expect(s.peekPendingInjectionCount()).toBe(0);
    expect(s.hasPendingInjections()).toBe(false);
    expect(s.drainPendingInjections()).toEqual([]);
  });
});

describe("Session.close — observability", () => {
  it("emits session_closed with correct sessionKey + reason", async () => {
    const agent = makeAgent();
    const s = makeSession(agent, "agent:main:app:obs:42");

    await s.close("cron_complete");

    expect(agent.events).toHaveLength(1);
    const [event] = agent.events;
    expect(event?.type).toBe("session_closed");
    if (event?.type === "session_closed") {
      expect(event.sessionKey).toBe("agent:main:app:obs:42");
      expect(event.reason).toBe("cron_complete");
      expect(typeof event.closedAt).toBe("number");
    }
  });

  it("omits reason field when none supplied", async () => {
    const agent = makeAgent();
    const s = makeSession(agent);
    await s.close();
    const [event] = agent.events;
    if (event?.type === "session_closed") {
      expect(event.reason).toBeUndefined();
    }
  });
});

describe("Agent.closeSession", () => {
  it("returns false for unknown sessionKey", async () => {
    // closeSession only needs the in-memory session registry for the
    // unknown-session path. Avoid the full Agent constructor so this
    // unit test remains deterministic under full-suite parallel load.
    const { Agent } = await import("./Agent.js");
    const agent = Object.assign(Object.create(Agent.prototype), {
      sessions: new Map<string, Session>(),
    }) as Agent;
    const ok = await agent.closeSession("does-not-exist");
    expect(ok).toBe(false);
  });

  it("removes the session from the registry and returns true", async () => {
    const { Agent } = await import("./Agent.js");
    const agent = new Agent({
      botId: "bot-x",
      userId: "user-x",
      workspaceRoot: "/tmp/session-close-agent-test-2",
      gatewayToken: "tok",
      apiProxyUrl: "http://localhost",
      chatProxyUrl: "http://localhost",
      redisUrl: "redis://localhost",
      model: "claude-opus-4-7",
    });
    const session = await agent.getOrCreateSession("agent:main:app:reg:1", {
      type: "app",
      channelId: "reg",
    });
    expect(agent.getSession("agent:main:app:reg:1")).toBe(session);
    const events: AgentLifecycleEvent[] = [];
    agent.onAgentEvent((e) => events.push(e));

    const ok = await agent.closeSession("agent:main:app:reg:1", "test");

    expect(ok).toBe(true);
    expect(agent.getSession("agent:main:app:reg:1")).toBeUndefined();
    expect(session.isClosed()).toBe(true);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe("session_closed");
  });
});

describe("cron wiring — non-durable session auto-close", () => {
  it("fireCron closes the synthetic session after the turn completes", async () => {
    // Integration-ish: use a real Agent but replace the LLM-backed
    // Session.runTurn with a spy so we don't need a live LLMClient.
    const { Agent } = await import("./Agent.js");
    const agent = new Agent({
      botId: "bot-cron",
      userId: "user-cron",
      workspaceRoot: "/tmp/session-close-cron-test",
      gatewayToken: "tok",
      apiProxyUrl: "http://localhost",
      chatProxyUrl: "http://localhost",
      redisUrl: "redis://localhost",
      model: "claude-opus-4-7",
    });

    // Patch getOrCreateSession so the synthetic cron session we
    // return has a stubbed runTurn — avoids the whole LLM stack.
    const origGetOrCreate = agent.getOrCreateSession.bind(agent);
    const stubRunTurn = vi.fn(async () => ({
      meta: {
        turnId: "t-1",
        sessionKey: "",
        startedAt: Date.now(),
        declaredRoute: "direct" as const,
        status: "committed" as const,
        usage: { inputTokens: 0, outputTokens: 0, costUsd: 0 },
      },
      assistantText: "",
    }));
    vi.spyOn(agent, "getOrCreateSession").mockImplementation(async (key, ref) => {
      const s = await origGetOrCreate(key, ref);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (s as any).runTurn = stubRunTurn;
      return s;
    });

    const record: CronRecord = {
      cronId: "cron-xyz",
      botId: "bot-cron",
      userId: "user-cron",
      expression: "* * * * *",
      prompt: "ping",
      deliveryChannel: { type: "app", channelId: "cron-ch" },
      enabled: true,
      createdAt: Date.now(),
      nextFireAt: Date.now(),
      consecutiveFailures: 0,
      durable: false,
      sessionKey: "agent:cron:app:cron-ch:cron-xyz",
    };

    const events: AgentLifecycleEvent[] = [];
    agent.onAgentEvent((e) => events.push(e));

    // Call the private fireCron via its bound name.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await (agent as any).fireCron(record);

    expect(stubRunTurn).toHaveBeenCalledTimes(1);
    const expectedKey = "agent:cron:app:cron-ch:cron-xyz";
    expect(agent.getSession(expectedKey)).toBeUndefined();
    const closedEvents = events.filter((e) => e.type === "session_closed");
    expect(closedEvents).toHaveLength(1);
    if (closedEvents[0]?.type === "session_closed") {
      expect(closedEvents[0].sessionKey).toBe(expectedKey);
      expect(closedEvents[0].reason).toBe("cron_complete");
    }
  });

  it("fireCron posts committed assistant text to the captured app channel", async () => {
    const { Agent } = await import("./Agent.js");
    const agent = new Agent({
      botId: "bot-cron",
      userId: "user-cron",
      workspaceRoot: "/tmp/session-close-cron-delivery-test",
      gatewayToken: "tok",
      apiProxyUrl: "http://localhost",
      chatProxyUrl: "http://chat-proxy.local",
      redisUrl: "redis://localhost",
      model: "claude-opus-4-7",
    });

    const origGetOrCreate = agent.getOrCreateSession.bind(agent);
    const stubRunTurn = vi.fn(async () => ({
      meta: {
        turnId: "t-deliver-1",
        sessionKey: "",
        startedAt: Date.now(),
        declaredRoute: "direct" as const,
        status: "committed" as const,
        usage: { inputTokens: 0, outputTokens: 0, costUsd: 0 },
      },
      assistantText: "테스트 브리핑입니다.",
    }));
    vi.spyOn(agent, "getOrCreateSession").mockImplementation(async (key, ref) => {
      const s = await origGetOrCreate(key, ref);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (s as any).runTurn = stubRunTurn;
      return s;
    });

    const fetchSpy = vi.fn(async () =>
      new Response(JSON.stringify({ status: "posted" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fetchSpy as typeof fetch;

    const record: CronRecord = {
      cronId: "cron-deliver",
      botId: "bot-cron",
      userId: "user-cron",
      expression: "* * * * *",
      prompt: "send briefing",
      deliveryChannel: { type: "app", channelId: "general" },
      enabled: true,
      createdAt: Date.now(),
      nextFireAt: Date.now(),
      consecutiveFailures: 0,
      durable: false,
      sessionKey: "agent:cron:app:general:cron-deliver",
    };

    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      await (agent as any).fireCron(record);
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy).toHaveBeenCalledWith(
      "http://chat-proxy.local/v1/bot-channels/post",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer tok",
          "Content-Type": "application/json",
        }),
        body: JSON.stringify({
          channel: "general",
          content: "테스트 브리핑입니다.",
        }),
      }),
    );

    const audit = await agent.auditLog.query({
      sessionKey: record.sessionKey,
      limit: 10,
    });
    expect(audit.entries.map((entry) => entry.event)).toContain(
      "cron_delivery_started",
    );
    expect(audit.entries.map((entry) => entry.event)).toContain(
      "cron_delivery_succeeded",
    );
    const succeeded = audit.entries.find(
      (entry) => entry.event === "cron_delivery_succeeded",
    );
    expect(succeeded?.turnId).toBe("t-deliver-1");
    expect(succeeded?.data).toMatchObject({
      cronId: "cron-deliver",
      channelType: "app",
      channelId: "general",
      textChars: "테스트 브리핑입니다.".length,
    });
  });

  it("fireCron audits delivery failures before surfacing them", async () => {
    const { Agent } = await import("./Agent.js");
    const agent = new Agent({
      botId: "bot-cron",
      userId: "user-cron",
      workspaceRoot: "/tmp/session-close-cron-delivery-fail-test",
      gatewayToken: "tok",
      apiProxyUrl: "http://localhost",
      chatProxyUrl: "http://chat-proxy.local",
      redisUrl: "redis://localhost",
      model: "claude-opus-4-7",
    });

    const origGetOrCreate = agent.getOrCreateSession.bind(agent);
    vi.spyOn(agent, "getOrCreateSession").mockImplementation(async (key, ref) => {
      const s = await origGetOrCreate(key, ref);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (s as any).runTurn = vi.fn(async () => ({
        meta: {
          turnId: "t-deliver-fail",
          sessionKey: "",
          startedAt: Date.now(),
          declaredRoute: "direct" as const,
          status: "committed" as const,
          usage: { inputTokens: 0, outputTokens: 0, costUsd: 0 },
        },
        assistantText: "실패해야 하는 브리핑입니다.",
      }));
      return s;
    });

    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () =>
      new Response("upstream unavailable", { status: 503 }),
    ) as typeof fetch;

    const record: CronRecord = {
      cronId: "cron-deliver-fail",
      botId: "bot-cron",
      userId: "user-cron",
      expression: "* * * * *",
      prompt: "send briefing",
      deliveryChannel: { type: "app", channelId: "general" },
      enabled: true,
      createdAt: Date.now(),
      nextFireAt: Date.now(),
      consecutiveFailures: 0,
      durable: false,
      sessionKey: "agent:cron:app:general:cron-deliver-fail",
    };

    try {
      await expect(
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (agent as any).fireCron(record),
      ).rejects.toThrow(/cron app delivery failed: HTTP 503/);
    } finally {
      globalThis.fetch = originalFetch;
    }

    const audit = await agent.auditLog.query({
      sessionKey: record.sessionKey,
      limit: 10,
    });
    const failed = audit.entries.find(
      (entry) => entry.event === "cron_delivery_failed",
    );
    expect(failed?.turnId).toBe("t-deliver-fail");
    expect(failed?.data).toMatchObject({
      cronId: "cron-deliver-fail",
      channelType: "app",
      channelId: "general",
      textChars: "실패해야 하는 브리핑입니다.".length,
      error: expect.stringContaining("HTTP 503"),
    });
  });
});
