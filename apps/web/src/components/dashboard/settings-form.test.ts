import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("SettingsForm local runtime wiring", () => {
  it("loads and saves OSS config through /v1/app/config", () => {
    const source = readFileSync(new URL("./settings-form.tsx", import.meta.url), "utf8");

    expect(source).toContain("/v1/app/config");
    expect(source).toContain('method: "PUT"');
    expect(source).not.toContain("/v1/settings");
    expect(source).not.toContain("model_selection");
    expect(source).not.toContain("custom_base_url");
  });

  it("offers only provider ids supported by the local CLI resolver", () => {
    const source = readFileSync(new URL("./settings-form.tsx", import.meta.url), "utf8");

    expect(source).toContain(
      'type ProviderName = "anthropic" | "openai" | "gemini" | "fireworks" | "openrouter"',
    );
    expect(source).toContain('{ value: "anthropic", label: "Anthropic" }');
    expect(source).toContain('{ value: "openai", label: "OpenAI" }');
    expect(source).toContain('{ value: "gemini", label: "Gemini" }');
    expect(source).toContain('{ value: "fireworks", label: "Fireworks" }');
    expect(source).toContain('{ value: "openrouter", label: "OpenRouter" }');
    expect(source).not.toContain('"google"');
    expect(source).not.toContain('"openai-compatible"');
    expect(source).not.toContain("OpenAI-Compatible");
  });

  it("renders the Model field as a provider-scoped preset dropdown with a Custom escape", () => {
    const source = readFileSync(new URL("./settings-form.tsx", import.meta.url), "utf8");

    // Presets come from the curated per-provider catalog, scoped to the selection.
    expect(source).toContain("LOCAL_RUNTIME_MODEL_PRESETS[provider]");
    // A dedicated "Custom…" option reveals the free-text input.
    expect(source).toContain("CUSTOM_MODEL_VALUE");
    expect(source).toContain("Custom… (enter model id)");
    expect(source).toContain("customModel");
    // Switching provider resets to that provider's default model.
    expect(source).toContain("LOCAL_RUNTIME_DEFAULT_MODEL[nextProvider]");
  });
});
