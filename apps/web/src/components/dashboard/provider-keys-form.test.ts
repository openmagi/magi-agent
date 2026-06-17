import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("ProviderKeysForm multi-provider key panel", () => {
  const source = readFileSync(new URL("./provider-keys-form.tsx", import.meta.url), "utf8");

  it("calls /v1/app/providers for GET and PUT", () => {
    expect(source).toContain("/v1/app/providers");
    expect(source).toContain('method: "PUT"');
  });

  it("does not reference dead endpoints like /v1/settings", () => {
    expect(source).not.toContain("/v1/settings");
    expect(source).not.toContain("model_selection");
  });

  it("uses a password input for the key (write-only)", () => {
    expect(source).toContain('type="password"');
  });

  it("includes all five supported providers", () => {
    expect(source).toContain('"anthropic"');
    expect(source).toContain('"openai"');
    expect(source).toContain('"gemini"');
    expect(source).toContain('"fireworks"');
    expect(source).toContain('"openrouter"');
  });

  it("does not read apiKey off the GET response (never renders key values)", () => {
    // The GET response shape has no apiKey field — the server never returns one.
    // Assert the source never plucks .apiKey from a snapshot row.
    expect(source).not.toMatch(/row\.apiKey/);
    expect(source).not.toMatch(/provider\.apiKey/);
    expect(source).not.toMatch(/item\.apiKey/);
    // The only apiKey usage must be inside the PUT payload builder, never read back.
    const getPhase = source.split('method: "PUT"')[0];
    expect(getPhase).not.toContain(".apiKey");
  });

  it("only sends non-empty keys (never sends apiKey:'' by accident)", () => {
    // Safety check: blank entries are skipped so existing keys are preserved.
    expect(source).toContain("key.length > 0");
    // There must be no unconditional apiKey: "" in the save path.
    expect(source).not.toMatch(/apiKey:\s*""/);
  });
});
