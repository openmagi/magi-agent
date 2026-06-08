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
      'type ProviderName = "anthropic" | "openai" | "gemini" | "fireworks"',
    );
    expect(source).toContain('{ value: "anthropic", label: "Anthropic" }');
    expect(source).toContain('{ value: "openai", label: "OpenAI" }');
    expect(source).toContain('{ value: "gemini", label: "Gemini" }');
    expect(source).toContain('{ value: "fireworks", label: "Fireworks" }');
    expect(source).not.toContain('"google"');
    expect(source).not.toContain('"openai-compatible"');
    expect(source).not.toContain("OpenAI-Compatible");
  });
});
