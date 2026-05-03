/**
 * Unit tests for the mid-turn injector beforeLLMCall hook (#86).
 */

import { describe, it, expect, vi } from "vitest";
import {
  buildInjectionMessages,
  makeMidTurnInjectorHook,
  wrapInjection,
} from "./midTurnInjector.js";
import type { Session } from "../../Session.js";
import type { HookContext } from "../types.js";
import type { UserMessage } from "../../util/types.js";
import type { LLMMessage } from "../../transport/LLMClient.js";

function makeCtx(sessionKey: string): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey,
    turnId: "turn-1",
    llm: {} as never,
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
  };
}

function stubSessionWithInjections(
  injections: UserMessage[] = [],
): { session: Session; drained: UserMessage[] } {
  const drained: UserMessage[] = [];
  const queue = [...injections];
  const session = {
    hasPendingInjections: () => queue.length > 0,
    drainPendingInjections: () => {
      const out = queue.splice(0);
      drained.push(...out);
      return out;
    },
  } as unknown as Session;
  return { session, drained };
}

const baseArgs = {
  messages: [
    { role: "user", content: [{ type: "text", text: "original question" }] },
  ] as LLMMessage[],
  tools: [],
  system: "you are a bot",
  iteration: 1,
};

describe("wrapInjection", () => {
  it("wraps text in a neutral follow-up user-message tag", () => {
    const wrapped = wrapInjection(2, "hello", "2026-04-20T12:00:00.000Z");
    expect(wrapped).toContain(
      '<follow_up_user_message seq="2" at="2026-04-20T12:00:00.000Z">',
    );
    expect(wrapped).toContain("hello");
    expect(wrapped).toContain("</follow_up_user_message>");
    expect(wrapped).not.toMatch(/injection/i);
  });
});

describe("buildInjectionMessages", () => {
  it("produces one user-role message per injection", () => {
    const messages = buildInjectionMessages([
      { text: "first", receivedAt: 1_000 },
      { text: "second", receivedAt: 2_000 },
    ]);
    expect(messages).toHaveLength(2);
    expect(messages[0]?.role).toBe("user");
    expect(messages[1]?.role).toBe("user");
    const first = messages[0]?.content as Array<{ type: string; text: string }>;
    expect(first[0]?.text).toContain("first");
    const second = messages[1]?.content as Array<{ type: string; text: string }>;
    expect(second[0]?.text).toContain("second");
  });

  it("starts sequence numbering at startSeq", () => {
    const messages = buildInjectionMessages(
      [{ text: "x", receivedAt: 1_000 }],
      7,
    );
    const first = messages[0]?.content as Array<{ type: string; text: string }>;
    expect(first[0]?.text).toContain('seq="7"');
  });

  it("returns empty array for empty input", () => {
    expect(buildInjectionMessages([])).toEqual([]);
  });
});

describe("makeMidTurnInjectorHook", () => {
  it("declares name, point, priority, non-blocking", () => {
    const hook = makeMidTurnInjectorHook({ agent: { getSession: () => undefined } });
    expect(hook.name).toBe("builtin:mid-turn-injector");
    expect(hook.point).toBe("beforeLLMCall");
    expect(hook.priority).toBe(3);
    expect(hook.blocking).toBe(false);
  });

  it("continues unchanged when session has no pending injections", async () => {
    const { session } = stubSessionWithInjections([]);
    const hook = makeMidTurnInjectorHook({ agent: { getSession: () => session } });
    const result = await hook.handler(baseArgs, makeCtx("s1"));
    expect(result).toEqual({ action: "continue" });
  });

  it("continues when session lookup returns undefined (evicted)", async () => {
    const hook = makeMidTurnInjectorHook({ agent: { getSession: () => undefined } });
    const result = await hook.handler(baseArgs, makeCtx("s1"));
    expect(result).toEqual({ action: "continue" });
  });

  it("drains pending injections and appends synthetic user messages", async () => {
    const { session, drained } = stubSessionWithInjections([
      { text: "inject-1", receivedAt: 1_000 },
      { text: "inject-2", receivedAt: 2_000 },
    ]);
    const hook = makeMidTurnInjectorHook({ agent: { getSession: () => session } });
    const ctx = makeCtx("s1");
    const result = await hook.handler(baseArgs, ctx);

    expect(result?.action).toBe("replace");
    if (result?.action !== "replace") throw new Error("expected replace");
    expect(result.value.messages).toHaveLength(3); // 1 original + 2 injected
    expect(result.value.system).toBe(baseArgs.system); // untouched
    expect(result.value.tools).toBe(baseArgs.tools); // untouched

    const injectedText = JSON.stringify(result.value.messages.slice(1));
    expect(injectedText).toContain("inject-1");
    expect(injectedText).toContain("inject-2");
    expect(injectedText).toContain("<follow_up_user_message");
    expect(injectedText).not.toMatch(/user_injection|prompt injection/i);

    expect(drained).toHaveLength(2);
    expect(ctx.log).toHaveBeenCalledWith(
      "info",
      "[mid-turn-injector] drained injections",
      expect.objectContaining({ count: 2 }),
    );
  });

  it("fails open if drain throws", async () => {
    const session = {
      hasPendingInjections: () => true,
      drainPendingInjections: () => {
        throw new Error("boom");
      },
    } as unknown as Session;
    const hook = makeMidTurnInjectorHook({ agent: { getSession: () => session } });
    const ctx = makeCtx("s1");
    const result = await hook.handler(baseArgs, ctx);
    expect(result).toEqual({ action: "continue" });
    expect(ctx.log).toHaveBeenCalledWith(
      "warn",
      "[mid-turn-injector] drain failed; turn continues",
      expect.any(Object),
    );
  });
});
