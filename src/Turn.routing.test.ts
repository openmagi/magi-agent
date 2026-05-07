import { describe, expect, it, vi } from "vitest";
import { Turn } from "./Turn.js";
import type { Session } from "./Session.js";
import type { RouteDecision } from "./routing/types.js";

function makeTurn(
  configModel: string,
  router: unknown,
  options: { runtimeModelOverride?: string; dynamicModel?: string } = {},
): Turn {
  const agent: {
    config: { model: string };
    router: unknown;
    resolveRuntimeModel?: () => Promise<string>;
  } = {
    config: { model: configModel },
    router,
  };
  if (options.dynamicModel) {
    agent.resolveRuntimeModel = vi.fn(async () => options.dynamicModel!);
  }
  const session = {
    meta: { sessionKey: "sess-1" },
    agent,
  } as unknown as Session;
  const sse = {
    agent: () => {},
  };
  return new Turn(
    session,
    { text: "hello", receivedAt: Date.now() },
    "turn-1",
    sse as never,
    "direct",
    { runtimeModelOverride: options.runtimeModelOverride },
  );
}

describe("Turn native routing", () => {
  it("keeps single-model turns on the configured model", async () => {
    const turn = makeTurn("claude-sonnet-4-6", null);

    const model = await (turn as unknown as {
      resolveEffectiveModel: (messages: [], tools: []) => Promise<string>;
    }).resolveEffectiveModel([], []);

    expect(model).toBe("claude-sonnet-4-6");
    expect(turn.meta.effectiveModel).toBe("claude-sonnet-4-6");
    expect(turn.meta.routeDecision).toBeUndefined();
  });

  it("resolves router keyword turns and records route metadata", async () => {
    const decision: RouteDecision = {
      profileId: "standard",
      tier: "DEEP",
      provider: "anthropic",
      model: "claude-opus-4-7",
      thinking: { type: "adaptive" },
      supportsTools: true,
      supportsImages: true,
      reason: "test",
      classifierUsed: true,
      classifierRaw: "DEEP",
      confidence: "classifier",
    };
    const router = {
      resolve: vi.fn().mockResolvedValue(decision),
    };
    const turn = makeTurn("magi-smart-router/auto", router);

    const model = await (turn as unknown as {
      resolveEffectiveModel: (
        messages: [{ role: "user"; content: "hi" }],
        tools: [{ name: "Bash" }],
      ) => Promise<string>;
    }).resolveEffectiveModel([{ role: "user", content: "hi" }], [{ name: "Bash" }]);

    expect(model).toBe("claude-opus-4-7");
    expect(turn.meta.effectiveModel).toBe("claude-opus-4-7");
    expect(turn.meta.routeDecision).toEqual(decision);
    expect(router.resolve).toHaveBeenCalledWith({
      configuredModel: "magi-smart-router/auto",
      messages: [{ role: "user", content: "hi" }],
      hasTools: true,
      hasImages: false,
    });
  });

  it("reuses the first route decision for all LLM calls within one turn", async () => {
    const decision: RouteDecision = {
      profileId: "standard",
      tier: "HEAVY",
      provider: "anthropic",
      model: "claude-opus-4-7",
      supportsTools: true,
      supportsImages: true,
      reason: "test",
      classifierUsed: true,
      classifierRaw: "HEAVY",
      confidence: "classifier",
    };
    const router = {
      resolve: vi.fn().mockResolvedValue(decision),
    };
    const turn = makeTurn("magi-smart-router/auto", router);
    const resolve = (turn as unknown as {
      resolveEffectiveModel: (
        messages: [{ role: "user"; content: string }],
        tools: [{ name: "Bash" }],
      ) => Promise<string>;
    }).resolveEffectiveModel.bind(turn);

    await resolve([{ role: "user", content: "run ls" }], [{ name: "Bash" }]);
    const second = await resolve([{ role: "user", content: "tool result" }], [{ name: "Bash" }]);

    expect(second).toBe("claude-opus-4-7");
    expect(router.resolve).toHaveBeenCalledTimes(1);
  });

  it("uses an explicit turn model override before dynamic bot model lookup", async () => {
    const router = {
      resolve: vi.fn(),
    };
    const turn = makeTurn("magi-smart-router/auto", router, {
      runtimeModelOverride: "openai/gpt-5.5-pro",
      dynamicModel: "openai/gpt-5.5",
    });

    const model = await (turn as unknown as {
      resolveEffectiveModel: (messages: [], tools: []) => Promise<string>;
    }).resolveEffectiveModel([], []);

    expect(model).toBe("openai/gpt-5.5-pro");
    expect(turn.meta.configuredModel).toBe("openai/gpt-5.5-pro");
    expect(router.resolve).not.toHaveBeenCalled();
  });
});
