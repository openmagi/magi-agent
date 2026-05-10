import { describe, expect, it } from "vitest";
import type { LLMEvent, LLMMessage, LLMStreamRequest } from "../transport/LLMClient.js";
import {
  getRoutingProfile,
  resolveExplicitModelPreference,
  routeSupportsTools,
} from "./profiles.js";
import { RouterEngine } from "./RouterEngine.js";
import { extractLatestUserText, messagesHaveImages } from "./messageText.js";

function fakeClassifier(reply: string): {
  stream: (req: LLMStreamRequest) => AsyncGenerator<LLMEvent, void, void>;
  calls: LLMStreamRequest[];
} {
  const calls: LLMStreamRequest[] = [];
  return {
    calls,
    async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
      calls.push(req);
      yield { kind: "text_delta", blockIndex: 0, delta: reply };
      yield {
        kind: "message_end",
        stopReason: "end_turn",
        usage: { inputTokens: 10, outputTokens: 1 },
      };
    },
  };
}

describe("routing profiles", () => {
  it("loads the standard hosted smart-router profile", () => {
    const profile = getRoutingProfile("standard");

    expect(profile.id).toBe("standard");
    expect(profile.classifierModel).toBe("gpt-5.4-mini");
    expect(profile.fallbackTier).toBe("MEDIUM");
    expect(profile.tiers.LIGHT).toMatchObject({
      tier: "LIGHT",
      provider: "openai",
      model: "gpt-5.4-mini",
    });
    expect(profile.tiers.MEDIUM).toMatchObject({
      tier: "MEDIUM",
      provider: "fireworks",
      model: "kimi-k2p6",
    });
    expect(profile.tiers.DEEP).toMatchObject({
      tier: "DEEP",
      provider: "openai",
      model: "gpt-5.5",
      thinking: { type: "adaptive" },
    });
    expect(profile.tiers.XDEEP).toMatchObject({
      tier: "XDEEP",
      provider: "google",
      model: "gemini-3.1-pro-preview",
      thinking: { type: "adaptive" },
    });
  });

  it("loads the premium hosted smart-router profile", () => {
    const profile = getRoutingProfile("premium");

    expect(profile.id).toBe("premium");
    expect(profile.classifierModel).toBe("claude-sonnet-4-6");
    expect(profile.fallbackTier).toBe("HEAVY");
    expect(profile.tiers.LIGHT).toMatchObject({
      tier: "LIGHT",
      provider: "anthropic",
      model: "claude-haiku-4-5-20251001",
    });
    expect(profile.tiers.MEDIUM).toMatchObject({
      tier: "MEDIUM",
      provider: "anthropic",
      model: "claude-opus-4-7",
    });
    expect(profile.tiers.DEEP).toMatchObject({
      tier: "DEEP",
      provider: "openai",
      model: "gpt-5.5",
      thinking: { type: "adaptive" },
    });
    expect(profile.tiers.XDEEP).toMatchObject({
      tier: "XDEEP",
      provider: "google",
      model: "gemini-3.1-pro-preview",
    });
  });

  it("loads local-first and hybrid profiles for self-hosted direct routing", () => {
    const local = getRoutingProfile("local-first");
    expect(local.id).toBe("local-first");
    expect(local.classifierModel).toBe("ollama/llama3.2:3b");
    expect(local.tiers.MEDIUM).toMatchObject({
      tier: "MEDIUM",
      provider: "ollama",
      model: "ollama/qwen2.5-coder:32b",
    });

    const hybrid = getRoutingProfile("hybrid");
    expect(hybrid.id).toBe("hybrid");
    expect(hybrid.tiers.LIGHT.provider).toBe("ollama");
    expect(hybrid.tiers.HEAVY.provider).toBe("anthropic");
  });

  it("maps explicit user model preferences through profile rules", () => {
    const profile = getRoutingProfile("standard");

    expect(resolveExplicitModelPreference(profile, "Opus로 짧게 답해줘")?.tier).toBe("HEAVY");
    expect(resolveExplicitModelPreference(profile, "Kimi로 분석해줘")?.tier).toBe("MEDIUM");
    expect(resolveExplicitModelPreference(profile, "GPT로 코드 리뷰해줘")?.tier).toBe("DEEP");
    expect(resolveExplicitModelPreference(profile, "Gemini로 긴 문서 분석해줘")?.tier).toBe("XDEEP");
    expect(resolveExplicitModelPreference(profile, "그냥 분석해줘")).toBeNull();
  });

  it("keeps tool-use turns away from routes that do not support tools", () => {
    const profile = getRoutingProfile("standard");

    expect(routeSupportsTools(profile.tiers.MEDIUM)).toBe(true);
    expect(routeSupportsTools(profile.tiers.LIGHT)).toBe(false);
  });
});

describe("extractLatestUserText", () => {
  it("extracts text from the last user message", () => {
    const messages: LLMMessage[] = [
      { role: "user", content: "first" },
      { role: "assistant", content: "answer" },
      { role: "user", content: [{ type: "text", text: "second" }] },
    ];

    expect(extractLatestUserText(messages)).toBe("second");
  });

  it("ignores images and tool results for classifier text", () => {
    const messages: LLMMessage[] = [
      {
        role: "user",
        content: [
          { type: "text", text: "read this" },
          {
            type: "image",
            source: { type: "base64", media_type: "image/png", data: "abc" },
          },
          { type: "tool_result", tool_use_id: "toolu_1", content: "ignored" },
        ],
      },
    ];

    expect(extractLatestUserText(messages)).toBe("read this");
    expect(messagesHaveImages(messages)).toBe(true);
  });
});

describe("RouterEngine", () => {
  it("uses fast paths without calling the classifier", async () => {
    const llm = fakeClassifier("XDEEP");
    const router = new RouterEngine({ llm, profileId: "standard" });

    const decision = await router.resolve({
      configuredModel: "magi-smart-router/auto",
      messages: [{ role: "user", content: "HEARTBEAT status" }],
      hasTools: true,
      hasImages: false,
    });

    expect(decision.tier).toBe("MEDIUM");
    expect(decision.classifierUsed).toBe(false);
    expect(decision.confidence).toBe("rule");
    expect(llm.calls).toHaveLength(0);
  });

  it("honors explicit model preferences before the classifier", async () => {
    const llm = fakeClassifier("LIGHT");
    const router = new RouterEngine({ llm, profileId: "standard" });

    const decision = await router.resolve({
      configuredModel: "magi-smart-router/auto",
      messages: [{ role: "user", content: "Opus로 한 문장 답해줘" }],
      hasTools: false,
      hasImages: false,
    });

    expect(decision.tier).toBe("HEAVY");
    expect(decision.model).toBe("claude-opus-4-7");
    expect(decision.classifierUsed).toBe(false);
  });

  it("uses the classifier when no rule matches", async () => {
    const llm = fakeClassifier("DEEP");
    const router = new RouterEngine({ llm, profileId: "standard" });

    const decision = await router.resolve({
      configuredModel: "magi-smart-router/auto",
      messages: [{ role: "user", content: "아키텍처 설계를 검토해줘" }],
      hasTools: true,
      hasImages: false,
    });

    expect(decision.tier).toBe("DEEP");
    expect(decision.model).toBe("gpt-5.5");
    expect(decision.thinking).toEqual({ type: "adaptive" });
    expect(decision.classifierUsed).toBe(true);
    expect((decision as { classifierModel?: string }).classifierModel).toBe("gpt-5.4-mini");
    expect(llm.calls[0]).toMatchObject({
      model: "gpt-5.4-mini",
      max_tokens: 10,
      temperature: 0,
      thinking: { type: "disabled" },
    });
  });

  it("falls back to MEDIUM when classifier output is invalid", async () => {
    const llm = fakeClassifier("BANANA");
    const router = new RouterEngine({ llm, profileId: "standard" });

    const decision = await router.resolve({
      configuredModel: "magi-smart-router/auto",
      messages: [{ role: "user", content: "분석해줘" }],
      hasTools: false,
      hasImages: false,
    });

    expect(decision.tier).toBe("MEDIUM");
    expect(decision.confidence).toBe("fallback");
  });

  it("escalates to the first tool-capable route when the chosen route lacks tools", async () => {
    const llm = fakeClassifier("LIGHT");
    const router = new RouterEngine({ llm, profileId: "standard" });

    const decision = await router.resolve({
      configuredModel: "magi-smart-router/auto",
      messages: [{ role: "user", content: "간단히 파일 읽어줘" }],
      hasTools: true,
      hasImages: false,
    });

    expect(decision.tier).toBe("MEDIUM");
    expect(decision.supportsTools).toBe(true);
  });

  it("keeps local-first tool turns away from local routes marked as tool-unsupported", async () => {
    const llm = fakeClassifier("DEEP");
    const router = new RouterEngine({ llm, profileId: "local-first" });

    const decision = await router.resolve({
      configuredModel: "magi-smart-router/auto",
      messages: [{ role: "user", content: "use a file tool for this" }],
      hasTools: true,
      hasImages: false,
    });

    expect(decision.tier).toBe("MEDIUM");
    expect(decision.model).toBe("ollama/qwen2.5-coder:32b");
    expect(decision.supportsTools).toBe(true);
  });

  it("escalates image turns to an image-capable route", async () => {
    const llm = fakeClassifier("MEDIUM");
    const router = new RouterEngine({ llm, profileId: "standard" });

    const decision = await router.resolve({
      configuredModel: "magi-smart-router/auto",
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: "이 이미지 설명해줘" },
            {
              type: "image",
              source: { type: "base64", media_type: "image/png", data: "abc" },
            },
          ],
        },
      ],
      hasTools: false,
      hasImages: true,
    });

    expect(decision.tier).toBe("HEAVY");
    expect(decision.supportsImages).toBe(true);
  });
});
