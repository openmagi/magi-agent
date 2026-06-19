import { describe, expect, it } from "vitest";
import {
  DEFAULT_REASONING_EFFORT,
  REASONING_EFFORT_VALUES,
  modelProviderForReasoning,
  modelSupportsReasoningEffort,
} from "./reasoning-effort";

describe("reasoning-effort", () => {
  it("exposes 4 user-facing levels with medium as the default", () => {
    expect(REASONING_EFFORT_VALUES).toEqual(["minimal", "low", "medium", "high"]);
    expect(DEFAULT_REASONING_EFFORT).toBe("medium");
  });

  it("maps runtime model ids to a provider key", () => {
    expect(modelProviderForReasoning("anthropic/claude-opus-4-8")).toBe("anthropic");
    expect(modelProviderForReasoning("openai/gpt-5.5")).toBe("openai");
    expect(modelProviderForReasoning("openai-codex/gpt-5.5")).toBe("openai");
    expect(modelProviderForReasoning("google/gemini-3.1-pro-preview")).toBe("gemini");
    expect(modelProviderForReasoning("fireworks/kimi-k2p6")).toBe("fireworks");
  });

  it("returns null for unclassifiable model strings (custom, local, empty)", () => {
    expect(modelProviderForReasoning("")).toBeNull();
    expect(modelProviderForReasoning("custom-model-id")).toBeNull();
    expect(modelProviderForReasoning("clawy_smart_routing")).toBeNull();
  });

  it("supports reasoning for anthropic / openai / gemini, not fireworks or local", () => {
    expect(modelSupportsReasoningEffort("anthropic/claude-sonnet-4-6")).toBe(true);
    expect(modelSupportsReasoningEffort("openai/gpt-5.5")).toBe(true);
    expect(modelSupportsReasoningEffort("openai-codex/gpt-5.5")).toBe(true);
    expect(modelSupportsReasoningEffort("google/gemini-3.5-flash")).toBe(true);
    expect(modelSupportsReasoningEffort("fireworks/kimi-k2p6")).toBe(false);
    expect(modelSupportsReasoningEffort("local/gemma-max")).toBe(false);
    expect(modelSupportsReasoningEffort("")).toBe(false);
  });
});
