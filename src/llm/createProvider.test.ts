import { describe, expect, it } from "vitest";
import { createProvider } from "./createProvider.js";
import { OpenAIProvider } from "./providers/OpenAIProvider.js";

describe("createProvider", () => {
  it("creates an OpenAI-compatible provider for local model servers without requiring an API key", () => {
    const provider = createProvider({
      provider: "openai-compatible",
      baseUrl: "http://127.0.0.1:11434/v1",
      defaultModel: "llama3.1",
    });

    expect(provider).toBeInstanceOf(OpenAIProvider);
  });

  it("requires baseUrl for OpenAI-compatible providers", () => {
    expect(() =>
      createProvider({
        provider: "openai-compatible",
        defaultModel: "llama3.1",
      }),
    ).toThrow(/baseUrl/);
  });

  it("keeps hosted provider API keys mandatory", () => {
    expect(() =>
      createProvider({
        provider: "openai",
        apiKey: "",
        defaultModel: "gpt-5.4",
      }),
    ).toThrow(/apiKey/);
  });
});
