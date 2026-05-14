/**
 * Unit tests for the onboarding-needed-check beforeTurnStart hook.
 * Design ref: docs/plans/2026-04-20-superpowers-plugin-design.md design #2.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  ONBOARDING_NUDGE_TEXT,
  isOnboardingSteerEnabled,
  looksLikeDecline,
  makeOnboardingNeededCheckHook,
  shouldSkipNudge,
} from "./onboardingNeededCheck.js";
import type { HookContext } from "../types.js";
import type { Session, SessionMeta } from "../../Session.js";

function makeCtx(sessionKey = "s1", classifierReply?: "YES" | "NO"): HookContext {
  const llm = classifierReply
    ? {
        async *stream() {
          yield { kind: "text_delta", delta: classifierReply, blockIndex: 0 };
          yield {
            kind: "message_end",
            stopReason: "end_turn",
            usage: { inputTokens: 1, outputTokens: 1 },
          };
        },
      }
    : {};
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey,
    turnId: "turn-1",
    llm: llm as never,
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
  };
}

function stubSession(
  overrides: Partial<SessionMeta> = {},
  turns = 0,
): Session {
  const meta: SessionMeta = {
    sessionKey: "s1",
    botId: "bot-test",
    channel: { type: "telegram", channelId: "1" },
    createdAt: 0,
    lastActivityAt: 0,
    ...overrides,
  };
  return {
    meta,
    budgetStats: () => ({
      turns,
      inputTokens: 0,
      outputTokens: 0,
      costUsd: 0,
    }),
  } as unknown as Session;
}

describe("looksLikeDecline", () => {
  it("matches common declines through the LLM classifier", async () => {
    const ctx = makeCtx("s1", "YES");
    await expect(looksLikeDecline("no", ctx)).resolves.toBe(true);
    await expect(looksLikeDecline("not now", ctx)).resolves.toBe(true);
    await expect(looksLikeDecline("later", ctx)).resolves.toBe(true);
    await expect(looksLikeDecline("skip", ctx)).resolves.toBe(true);
    await expect(looksLikeDecline("나중에", ctx)).resolves.toBe(true);
    await expect(looksLikeDecline("안 할래", ctx)).resolves.toBe(true);
  });

  it("does not match affirmative text", async () => {
    const ctx = makeCtx("s1", "NO");
    await expect(looksLikeDecline("yes please", ctx)).resolves.toBe(false);
    await expect(looksLikeDecline("sure", ctx)).resolves.toBe(false);
    await expect(looksLikeDecline("", ctx)).resolves.toBe(false);
  });
});

describe("isOnboardingSteerEnabled", () => {
  it("default is on", () => {
    expect(isOnboardingSteerEnabled(undefined)).toBe(true);
  });
  it("explicit off disables", () => {
    expect(isOnboardingSteerEnabled("off")).toBe(false);
  });
});

describe("shouldSkipNudge", () => {
  it("skips when already onboarded", () => {
    expect(shouldSkipNudge(stubSession({ onboarded: true }))).toBe(true);
  });
  it("skips when declines >= 2", () => {
    expect(shouldSkipNudge(stubSession({ onboardingDeclines: 2 }))).toBe(true);
  });
  it("skips when session already has committed turns", () => {
    expect(shouldSkipNudge(stubSession({}, /*turns=*/ 3))).toBe(true);
  });
  it("skips when a session-resume packet was queued for this session", () => {
    expect(shouldSkipNudge(stubSession({ resumeSeededAt: Date.now() }))).toBe(true);
  });
  it("does NOT skip on a fresh first-turn non-onboarded session", () => {
    expect(shouldSkipNudge(stubSession({}))).toBe(false);
  });
});

describe("makeOnboardingNeededCheckHook", () => {
  const prevEnv = process.env.MAGI_ONBOARDING_STEER;
  beforeEach(() => {
    delete process.env.MAGI_ONBOARDING_STEER;
  });
  afterEach(() => {
    if (prevEnv === undefined) delete process.env.MAGI_ONBOARDING_STEER;
    else process.env.MAGI_ONBOARDING_STEER = prevEnv;
  });

  it("declares name, point, priority, non-blocking", () => {
    const hook = makeOnboardingNeededCheckHook({
      agent: { getSession: () => undefined },
    });
    expect(hook.name).toBe("builtin:onboarding-needed-check");
    expect(hook.point).toBe("beforeTurnStart");
    expect(hook.priority).toBe(6);
    expect(hook.blocking).toBe(false);
  });

  it("emits onboarding_nudge on a first-turn non-onboarded session", async () => {
    const session = stubSession({});
    const hook = makeOnboardingNeededCheckHook({
      agent: { getSession: () => session },
    });
    const ctx = makeCtx("s1", "NO");
    const result = await hook.handler(
      { userMessage: "help me plan my day" },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(ctx.emit).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "onboarding_nudge",
        text: ONBOARDING_NUDGE_TEXT,
      }),
    );
  });

  it("skips when session.meta.onboarded=true", async () => {
    const session = stubSession({ onboarded: true });
    const hook = makeOnboardingNeededCheckHook({
      agent: { getSession: () => session },
    });
    const ctx = makeCtx("s1", "NO");
    const result = await hook.handler(
      { userMessage: "help me plan my day" },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(ctx.emit).not.toHaveBeenCalled();
  });

  it("skips when declines has reached 2", async () => {
    const session = stubSession({ onboardingDeclines: 2 });
    const hook = makeOnboardingNeededCheckHook({
      agent: { getSession: () => session },
    });
    const ctx = makeCtx("s1", "YES");
    const result = await hook.handler(
      { userMessage: "another request" },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(ctx.emit).not.toHaveBeenCalled();
  });

  it("increments declines counter on decline reply", async () => {
    const session = stubSession({ onboardingDeclines: 1 });
    const hook = makeOnboardingNeededCheckHook({
      agent: { getSession: () => session },
    });
    const ctx = makeCtx("s1", "YES");
    const result = await hook.handler(
      { userMessage: "not now, later" },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(session.meta.onboardingDeclines).toBe(2);
    // A decline message is not a nudge signal.
    expect(ctx.emit).not.toHaveBeenCalled();
  });

  it("skips entirely when env gate is off", async () => {
    process.env.MAGI_ONBOARDING_STEER = "off";
    const session = stubSession({});
    const hook = makeOnboardingNeededCheckHook({
      agent: { getSession: () => session },
    });
    const ctx = makeCtx();
    const result = await hook.handler(
      { userMessage: "help me plan my day" },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(ctx.emit).not.toHaveBeenCalled();
  });
});
