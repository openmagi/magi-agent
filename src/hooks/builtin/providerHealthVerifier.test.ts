import { describe, expect, it } from "vitest";
import type { HookContext } from "../types.js";
import { makeProviderHealthVerifierHook } from "./providerHealthVerifier.js";

function makeCtx(providerHealth: HookContext["providerHealth"]): HookContext {
  return {
    botId: "bot-1",
    userId: "user-1",
    sessionKey: "session-1",
    turnId: "turn-1",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "gpt-5.4-mini",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
    providerHealth,
  };
}

function args(overrides: Partial<Parameters<ReturnType<typeof makeProviderHealthVerifierHook>["handler"]>[0]> = {}) {
  return {
    assistantText: "배포 순서는 이렇습니다.",
    toolCallCount: 0,
    toolReadHappened: false,
    userMessage: "프로덕션 배포 순서 알려줘",
    retryCount: 0,
    ...overrides,
  };
}

describe("providerHealthVerifier", () => {
  it("blocks exactness-sensitive answers without same-turn evidence under degraded provider health", async () => {
    const hook = makeProviderHealthVerifierHook();
    const result = await hook.handler(
      args(),
      makeCtx({
        provider: "openai",
        model: "gpt-5.4-mini",
        state: "degraded",
        confidence: "high",
        summary: "local rate_limit=3",
        routeReason: "primary",
      }),
    );

    expect(result).toMatchObject({ action: "block" });
    expect(result?.action === "block" ? result.reason : "").toContain("[RETRY:PROVIDER_HEALTH]");
  });

  it("allows degraded-provider exactness-sensitive answers when a read tool fired", async () => {
    const hook = makeProviderHealthVerifierHook();
    const result = await hook.handler(
      args({ toolReadHappened: true }),
      makeCtx({
        provider: "openai",
        model: "gpt-5.4-mini",
        state: "degraded",
        confidence: "high",
        summary: "local rate_limit=3",
        routeReason: "primary",
      }),
    );

    expect(result).toMatchObject({ action: "continue" });
  });

  it("allows ok-provider exactness-sensitive answers without changing policy", async () => {
    const hook = makeProviderHealthVerifierHook();
    const result = await hook.handler(
      args(),
      makeCtx({
        provider: "openai",
        model: "gpt-5.4-mini",
        state: "ok",
        confidence: "high",
        summary: "public ok",
        routeReason: "primary",
      }),
    );

    expect(result).toMatchObject({ action: "continue" });
  });

  it("allows casual answers under degraded provider health", async () => {
    const hook = makeProviderHealthVerifierHook();
    const result = await hook.handler(
      args({ userMessage: "안녕 오늘 기분 어때?" }),
      makeCtx({
        provider: "openai",
        model: "gpt-5.4-mini",
        state: "degraded",
        confidence: "high",
        summary: "local rate_limit=3",
        routeReason: "primary",
      }),
    );

    expect(result).toMatchObject({ action: "continue" });
  });
});
