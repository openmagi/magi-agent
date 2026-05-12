/**
 * T4-17 — Model capability registry tests.
 *
 * Covers:
 *   - getCapability for known + unknown models
 *   - computeUsd math for a known model + unknown-model fallback
 *   - shouldEnableThinkingByDefault for opus (true) + haiku (false)
 *   - MODEL_CAPABILITIES contains the expected ids
 */

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  MODEL_CAPABILITIES,
  getCapability,
  computeUsd,
  reloadModelRegistryForTests,
  registerModelCapability,
  resetCustomModelCapabilitiesForTests,
  shouldEnableThinkingByDefault,
} from "./modelCapabilities.js";

beforeEach(() => {
  vi.stubEnv("MAGI_MODEL_REGISTRY", "");
  vi.stubEnv("MODEL_REGISTRY_PATH", "");
  resetCustomModelCapabilitiesForTests();
  reloadModelRegistryForTests();
});

afterEach(() => {
  vi.stubEnv("MAGI_MODEL_REGISTRY", "");
  vi.stubEnv("MODEL_REGISTRY_PATH", "");
  resetCustomModelCapabilitiesForTests();
  reloadModelRegistryForTests();
  vi.unstubAllEnvs();
});

function withRegistryYaml(contents: string): { dir: string; file: string } {
  const dir = mkdtempSync(join(tmpdir(), "model-capability-registry-"));
  const file = join(dir, "model-registry.yaml");
  writeFileSync(file, contents, "utf8");
  return { dir, file };
}

describe("getCapability", () => {
  it("returns the full record for a known model", () => {
    const cap = getCapability("claude-opus-4-7");
    expect(cap).not.toBeNull();
    expect(cap).toEqual({
      id: "claude-opus-4-7",
      supportsThinking: true,
      maxOutputTokens: 32_000,
      contextWindow: 900_000,
      inputUsdPerMtok: 15,
      outputUsdPerMtok: 75,
    });
  });

  it("returns null for an unknown model", () => {
    expect(getCapability("unknown-model-9")).toBeNull();
  });

  it("contains the expected model ids", () => {
    expect(MODEL_CAPABILITIES["claude-opus-4-7"]).toBeDefined();
    expect(MODEL_CAPABILITIES["claude-opus-4-6"]).toBeDefined();
    expect(MODEL_CAPABILITIES["claude-sonnet-4-6"]).toBeDefined();
    expect(MODEL_CAPABILITIES["claude-haiku-4-5-20251001"]).toBeDefined();
    expect(MODEL_CAPABILITIES["openai/gpt-5.4-nano"]).toBeDefined();
    expect(MODEL_CAPABILITIES["openai/gpt-5.4-mini"]).toBeDefined();
    expect(MODEL_CAPABILITIES["openai/gpt-5.5"]).toBeDefined();
    expect(MODEL_CAPABILITIES["openai/gpt-5.5-pro"]).toBeDefined();
    expect(MODEL_CAPABILITIES["openai-codex/gpt-5.5"]).toBeDefined();
    expect(MODEL_CAPABILITIES["local/gemma-fast"]).toBeDefined();
    expect(MODEL_CAPABILITIES["local/gemma-max"]).toBeDefined();
    expect(MODEL_CAPABILITIES["local/qwen-uncensored"]).toBeDefined();
    expect(MODEL_CAPABILITIES["gpt-5.4-nano"]).toBeDefined();
    expect(MODEL_CAPABILITIES["gpt-5.4-mini"]).toBeDefined();
    expect(MODEL_CAPABILITIES["gpt-5.5"]).toBeDefined();
    expect(MODEL_CAPABILITIES["gpt-5-nano"]).toBeDefined();
    expect(MODEL_CAPABILITIES["kimi-k2p6"]).toBeDefined();
    expect(MODEL_CAPABILITIES["gemini-3.1-pro-preview"]).toBeDefined();
    expect(MODEL_CAPABILITIES["magi-smart-router/auto"]).toBeUndefined();
    expect(MODEL_CAPABILITIES["big-dic-router/auto"]).toBeUndefined();
  });

  it("contains Mac Studio local model capabilities", () => {
    expect(getCapability("local/gemma-fast")).toMatchObject({
      supportsThinking: false,
      maxOutputTokens: 8192,
      contextWindow: 131072,
    });
    expect(getCapability("local/gemma-max")).toMatchObject({
      supportsThinking: false,
      maxOutputTokens: 8192,
      contextWindow: 131072,
    });
    expect(getCapability("local/qwen-uncensored")).toMatchObject({
      supportsThinking: false,
      maxOutputTokens: 8192,
      contextWindow: 131072,
    });
    expect(shouldEnableThinkingByDefault("local/gemma-fast")).toBe(false);
  });

  it("allows runtime registration of custom local model capabilities", () => {
    registerModelCapability({
      id: "llama3.1",
      supportsThinking: false,
      maxOutputTokens: 4096,
      contextWindow: 65_536,
      inputUsdPerMtok: 0,
      outputUsdPerMtok: 0,
    });

    expect(getCapability("llama3.1")).toMatchObject({
      id: "llama3.1",
      maxOutputTokens: 4096,
      contextWindow: 65_536,
    });
    expect(computeUsd("llama3.1", 1_000_000, 1_000_000)).toBe(0);
    expect(shouldEnableThinkingByDefault("llama3.1")).toBe(false);
  });

  it("derives zero-cost fallback capabilities for OpenAI-compatible local model prefixes", () => {
    expect(getCapability("ollama/llama3.3:70b")).toMatchObject({
      id: "ollama/llama3.3:70b",
      supportsThinking: false,
      maxOutputTokens: 8192,
      contextWindow: 131072,
      inputUsdPerMtok: 0,
      outputUsdPerMtok: 0,
    });
    expect(getCapability("vllm/qwen2.5-72b")).toMatchObject({
      id: "vllm/qwen2.5-72b",
      contextWindow: 131072,
    });
    expect(getCapability("tgi/mistral-large")).toMatchObject({
      id: "tgi/mistral-large",
      contextWindow: 131072,
    });
    expect(getCapability("localai/llama3")).toMatchObject({
      id: "localai/llama3",
      contextWindow: 131072,
    });
    expect(getCapability("custom/qwen2.5-72b")).toMatchObject({
      id: "custom/qwen2.5-72b",
      contextWindow: 131072,
    });
    expect(computeUsd("ollama/llama3.3:70b", 1_000_000, 1_000_000)).toBe(0);
    expect(shouldEnableThinkingByDefault("ollama/deepseek-r1:32b")).toBe(false);
  });

  it("derives OpenRouter fallback capabilities without pretending pricing is known", () => {
    expect(getCapability("openrouter/anthropic/claude-opus-4-7")).toMatchObject({
      id: "openrouter/anthropic/claude-opus-4-7",
      supportsThinking: false,
      maxOutputTokens: 8192,
      contextWindow: 131072,
      inputUsdPerMtok: 0,
      outputUsdPerMtok: 0,
    });
  });

  it("recognizes provider-prefixed runtime model ids used by provisioning and api-proxy", () => {
    expect(getCapability("anthropic/claude-opus-4-7")).toMatchObject({
      id: "claude-opus-4-7",
      contextWindow: 900_000,
    });
    expect(getCapability("anthropic/claude-sonnet-4-6")).toMatchObject({
      id: "claude-sonnet-4-6",
      contextWindow: 200_000,
    });
    expect(getCapability("fireworks/kimi-k2p6")).toMatchObject({
      contextWindow: 262_144,
      maxOutputTokens: 32_768,
    });
    expect(getCapability("google/gemini-3.1-pro-preview")).toMatchObject({
      contextWindow: 1_048_576,
      maxOutputTokens: 65_536,
    });
  });

  it("uses the YAML registry when MAGI_MODEL_REGISTRY is enabled", () => {
    const { dir, file } = withRegistryYaml(`models:
  yaml-only-model:
    provider: anthropic
    context_window: 345678
    max_output: 12345
    thinking: { type: adaptive }
    temperature: 0.4
    capabilities: [tool_use, vision, extended_thinking]
    edit_format: search_replace
    pricing: { input_per_mtok: 2, output_per_mtok: 8 }
`);
    try {
      vi.stubEnv("MAGI_MODEL_REGISTRY", "1");
      vi.stubEnv("MODEL_REGISTRY_PATH", file);
      reloadModelRegistryForTests();

      expect(getCapability("yaml-only-model")).toEqual({
        id: "yaml-only-model",
        supportsThinking: true,
        maxOutputTokens: 12345,
        contextWindow: 345678,
        inputUsdPerMtok: 2,
        outputUsdPerMtok: 8,
      });
      expect(computeUsd("yaml-only-model", 1_000_000, 1_000_000)).toBeCloseTo(10, 6);
      expect(shouldEnableThinkingByDefault("yaml-only-model")).toBe(true);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("falls back to hardcoded capabilities when YAML is empty", () => {
    const { dir, file } = withRegistryYaml("models: {}\n");
    try {
      vi.stubEnv("MAGI_MODEL_REGISTRY", "1");
      vi.stubEnv("MODEL_REGISTRY_PATH", file);
      reloadModelRegistryForTests();

      expect(getCapability("claude-sonnet-4-6")).toMatchObject({
        id: "claude-sonnet-4-6",
        contextWindow: 200000,
        maxOutputTokens: 16000,
      });
      expect(getCapability("missing-from-yaml-and-fallback")).toBeNull();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("computeUsd", () => {
  it("computes USD correctly for a known model (Opus 4.7: $15 in / $75 out per Mtok)", () => {
    // 1M in × $15 + 1M out × $75 = $90
    expect(computeUsd("claude-opus-4-7", 1_000_000, 1_000_000)).toBeCloseTo(
      90,
      6,
    );
    // 12k in + 3k out = 0.18 + 0.225 = 0.405
    expect(computeUsd("claude-opus-4-7", 12_000, 3_000)).toBeCloseTo(0.405, 6);
  });

  it("returns 0 for unknown model (fail-open)", () => {
    expect(computeUsd("claude-mystery-9", 1_000_000, 1_000_000)).toBe(0);
    expect(computeUsd("", 100, 100)).toBe(0);
  });

  it("computes USD correctly for Haiku ($1 in / $5 out)", () => {
    // 1M in × $1 + 1M out × $5 = $6
    expect(
      computeUsd("claude-haiku-4-5-20251001", 1_000_000, 1_000_000),
    ).toBeCloseTo(6, 6);
  });

  it("computes USD for provider-prefixed runtime model ids", () => {
    expect(computeUsd("anthropic/claude-opus-4-7", 1_000_000, 1_000_000)).toBeCloseTo(90, 6);
    expect(computeUsd("fireworks/kimi-k2p6", 1_000_000, 1_000_000)).toBeCloseTo(4.95, 6);
    expect(computeUsd("google/gemini-3.1-pro-preview", 1_000_000, 1_000_000)).toBeCloseTo(14, 6);
  });

  it("computes USD correctly for GPT-5.5 ($5 in / $30 out)", () => {
    expect(computeUsd("openai/gpt-5.5", 1_000_000, 1_000_000)).toBeCloseTo(35, 6);
    expect(computeUsd("openai-codex/gpt-5.5", 1_000_000, 1_000_000)).toBeCloseTo(35, 6);
  });

  it("computes USD correctly for GPT-5.5 Pro ($30 in / $180 out)", () => {
    expect(computeUsd("openai/gpt-5.5-pro", 1_000_000, 1_000_000)).toBeCloseTo(210, 6);
  });

  it("computes non-zero costs for bare routed model ids", () => {
    expect(computeUsd("gpt-5.4-nano", 1_000_000, 1_000_000)).toBeGreaterThan(0);
    expect(computeUsd("gpt-5.4-mini", 1_000_000, 1_000_000)).toBeGreaterThan(0);
    expect(computeUsd("kimi-k2p6", 1_000_000, 1_000_000)).toBeGreaterThan(0);
  });
});

describe("shouldEnableThinkingByDefault", () => {
  it("returns true for opus (extended thinking supported)", () => {
    expect(shouldEnableThinkingByDefault("claude-opus-4-7")).toBe(true);
    expect(shouldEnableThinkingByDefault("claude-opus-4-6")).toBe(true);
  });

  it("returns true for sonnet", () => {
    expect(shouldEnableThinkingByDefault("claude-sonnet-4-6")).toBe(true);
  });

  it("returns false for haiku (no extended thinking)", () => {
    expect(shouldEnableThinkingByDefault("claude-haiku-4-5-20251001")).toBe(
      false,
    );
  });

  it("returns false for GPT-5.5 because api-proxy uses reasoning_effort instead", () => {
    expect(shouldEnableThinkingByDefault("openai/gpt-5.5")).toBe(false);
    expect(shouldEnableThinkingByDefault("openai/gpt-5.5-pro")).toBe(false);
    expect(shouldEnableThinkingByDefault("openai-codex/gpt-5.5")).toBe(false);
  });

  it("returns false for non-Anthropic routed models", () => {
    expect(shouldEnableThinkingByDefault("gpt-5.4-nano")).toBe(false);
    expect(shouldEnableThinkingByDefault("gpt-5.4-mini")).toBe(false);
    expect(shouldEnableThinkingByDefault("kimi-k2p6")).toBe(false);
    expect(shouldEnableThinkingByDefault("gemini-3.1-pro-preview")).toBe(false);
  });

  it("returns false for unknown models (fail-closed on thinking)", () => {
    expect(shouldEnableThinkingByDefault("unknown-model")).toBe(false);
  });
});
