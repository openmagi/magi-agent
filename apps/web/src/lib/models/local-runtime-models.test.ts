import { describe, expect, it } from "vitest";
import {
  CUSTOM_MODEL_VALUE,
  LOCAL_RUNTIME_DEFAULT_MODEL,
  LOCAL_RUNTIME_MODEL_PRESETS,
  isPresetModel,
  type LocalRuntimeProvider,
} from "./local-runtime-models";

const PROVIDERS: LocalRuntimeProvider[] = [
  "anthropic",
  "openai",
  "gemini",
  "fireworks",
  "openrouter",
];

describe("local-runtime-models", () => {
  it("covers exactly the local CLI providers", () => {
    expect(Object.keys(LOCAL_RUNTIME_MODEL_PRESETS).sort()).toEqual(
      [...PROVIDERS].sort(),
    );
  });

  it("each provider's default model is itself a preset (dropdown can show it)", () => {
    for (const provider of PROVIDERS) {
      expect(isPresetModel(provider, LOCAL_RUNTIME_DEFAULT_MODEL[provider])).toBe(true);
    }
  });

  it("matches the local CLI resolver defaults (cli/providers.py _DEFAULT_MODEL)", () => {
    expect(LOCAL_RUNTIME_DEFAULT_MODEL).toEqual({
      anthropic: "claude-sonnet-5",
      openai: "gpt-5.5",
      gemini: "gemini-3.5-flash",
      fireworks: "kimi-k2p6",
      openrouter: "openai/gpt-5.5",
    });
  });

  it("uses bare model ids the local resolver expects (no litellm provider prefix)", () => {
    // Fireworks expects bare ids (e.g. `kimi-k2p6`); the resolver applies the
    // `fireworks_ai/` prefix at call time. The legacy retired
    // `accounts/fireworks/models/kimi-k2-instruct` id MUST NOT be offered.
    const fireworks = LOCAL_RUNTIME_MODEL_PRESETS.fireworks.map((m) => m.value);
    expect(fireworks).toContain("kimi-k2p6");
    expect(fireworks).not.toContain("accounts/fireworks/models/kimi-k2-instruct");
    // Non-openrouter providers must NOT carry a `<provider>/` slug prefix —
    // the resolver adds the litellm prefix itself. OpenRouter is the exception:
    // its id IS a `<vendor>/<model>` slug.
    for (const provider of PROVIDERS) {
      if (provider === "openrouter") continue;
      for (const option of LOCAL_RUNTIME_MODEL_PRESETS[provider]) {
        expect(option.value.startsWith(`${provider}/`)).toBe(false);
      }
    }
  });

  it("offers the current frontier Anthropic model (Opus 4.8) and keeps 4.6 for back-compat", () => {
    const anthropic = LOCAL_RUNTIME_MODEL_PRESETS.anthropic.map((m) => m.value);
    expect(anthropic).toContain("claude-opus-4-8");
    expect(anthropic).toContain("claude-sonnet-5");
    expect(anthropic).toContain("claude-sonnet-4-6");
    expect(anthropic).toContain("claude-haiku-4-5");
    expect(anthropic).toContain("claude-opus-4-6"); // legacy, kept selectable
  });

  it("isPresetModel distinguishes presets from custom ids", () => {
    expect(isPresetModel("openai", "gpt-5.5")).toBe(true);
    expect(isPresetModel("openai", "gpt-4.1")).toBe(false);
    expect(isPresetModel("anthropic", CUSTOM_MODEL_VALUE)).toBe(false);
  });

  it("exposes a stable custom sentinel that is never a real model id", () => {
    expect(CUSTOM_MODEL_VALUE).toBe("__custom__");
    for (const provider of PROVIDERS) {
      expect(isPresetModel(provider, CUSTOM_MODEL_VALUE)).toBe(false);
    }
  });
});
