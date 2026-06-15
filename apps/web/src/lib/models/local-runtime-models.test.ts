import { describe, expect, it } from "vitest";
import {
  CUSTOM_MODEL_VALUE,
  LOCAL_RUNTIME_DEFAULT_MODEL,
  LOCAL_RUNTIME_MODEL_PRESETS,
  isPresetModel,
  type LocalRuntimeProvider,
} from "./local-runtime-models";

const PROVIDERS: LocalRuntimeProvider[] = ["anthropic", "openai", "gemini", "fireworks"];

describe("local-runtime-models", () => {
  it("covers exactly the four local CLI providers", () => {
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
      anthropic: "claude-sonnet-4-6",
      openai: "gpt-5.5",
      gemini: "gemini-3.5-flash",
      fireworks: "accounts/fireworks/models/kimi-k2-instruct",
    });
  });

  it("uses raw litellm model ids (fireworks = accounts path, not the hosted alias)", () => {
    const fireworks = LOCAL_RUNTIME_MODEL_PRESETS.fireworks.map((m) => m.value);
    expect(fireworks).toContain("accounts/fireworks/models/kimi-k2-instruct");
    expect(fireworks).not.toContain("kimi-k2p6"); // hosted api-proxy alias, invalid locally
    // No values carry a `<provider>/` litellm prefix — the resolver adds it.
    for (const provider of PROVIDERS) {
      for (const option of LOCAL_RUNTIME_MODEL_PRESETS[provider]) {
        if (provider !== "fireworks") {
          expect(option.value.startsWith(`${provider}/`)).toBe(false);
        }
      }
    }
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
