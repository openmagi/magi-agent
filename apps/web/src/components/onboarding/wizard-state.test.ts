import { describe, expect, it, vi } from "vitest";
import {
  ALLOWED_PROVIDERS,
  PROVIDER_LABELS,
  WIZARD_STEPS,
  applyProviderChange,
  buildConfigPayload,
  canAdvance,
  defaultModelForProvider,
  isAllowedProvider,
  nextStep,
  prevStep,
  providerKeyHint,
  resolveInitialProvider,
  resolveSubmitModel,
  submitProviderConfig,
  validateProviderKeyStep,
} from "./wizard-state";

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("wizard step flow", () => {
  it("orders steps provider-key -> model -> integrations -> done", () => {
    expect(WIZARD_STEPS).toEqual(["provider-key", "model", "integrations", "done"]);
  });

  it("advances forward through the flow and clamps at done", () => {
    expect(nextStep("provider-key")).toBe("model");
    expect(nextStep("model")).toBe("integrations");
    expect(nextStep("integrations")).toBe("done");
    expect(nextStep("done")).toBe("done");
  });

  it("walks backward through the flow and clamps at the first step", () => {
    expect(prevStep("done")).toBe("integrations");
    expect(prevStep("integrations")).toBe("model");
    expect(prevStep("model")).toBe("provider-key");
    expect(prevStep("provider-key")).toBe("provider-key");
  });
});

describe("defaultModelForProvider", () => {
  it("returns the catalog default for a known provider", () => {
    expect(defaultModelForProvider("anthropic")).toBe("claude-sonnet-5");
    expect(defaultModelForProvider("openai")).toBe("gpt-5.5");
    expect(defaultModelForProvider("fireworks")).toBe("kimi-k2p6");
  });
});

describe("provider allow-list", () => {
  it("exposes the five supported providers", () => {
    expect(ALLOWED_PROVIDERS).toEqual([
      "anthropic",
      "openai",
      "gemini",
      "fireworks",
      "openrouter",
    ]);
  });

  it("narrows arbitrary strings to allowed providers", () => {
    expect(isAllowedProvider("anthropic")).toBe(true);
    expect(isAllowedProvider("google")).toBe(false);
    expect(isAllowedProvider("")).toBe(false);
  });

  it("maps each provider to a human label", () => {
    expect(PROVIDER_LABELS.anthropic).toBe("Anthropic");
    expect(PROVIDER_LABELS.openrouter).toBe("OpenRouter");
  });

  it("offers a key hint for each provider", () => {
    for (const provider of ALLOWED_PROVIDERS) {
      expect(providerKeyHint(provider).length).toBeGreaterThan(0);
    }
  });
});

describe("validateProviderKeyStep", () => {
  it("accepts an allowed provider with a non-empty key", () => {
    expect(validateProviderKeyStep({ provider: "anthropic", apiKey: "sk-123" })).toBe(true);
  });

  it("rejects a blank or whitespace-only key", () => {
    expect(validateProviderKeyStep({ provider: "anthropic", apiKey: "" })).toBe(false);
    expect(validateProviderKeyStep({ provider: "anthropic", apiKey: "   " })).toBe(false);
  });

  it("rejects an unsupported provider", () => {
    expect(validateProviderKeyStep({ provider: "google", apiKey: "sk-123" })).toBe(false);
  });
});

describe("canAdvance", () => {
  it("blocks the provider-key step until valid", () => {
    expect(canAdvance("provider-key", { provider: "anthropic", apiKey: "" })).toBe(false);
    expect(canAdvance("provider-key", { provider: "anthropic", apiKey: "sk" })).toBe(true);
  });

  it("requires a non-empty model on the model step", () => {
    expect(canAdvance("model", { provider: "anthropic", apiKey: "sk", model: "" })).toBe(false);
    expect(
      canAdvance("model", { provider: "anthropic", apiKey: "sk", model: "claude-sonnet-4-6" }),
    ).toBe(true);
  });

  it("always allows advancing the optional integrations step", () => {
    expect(canAdvance("integrations", { provider: "anthropic", apiKey: "sk", model: "x" })).toBe(
      true,
    );
  });
});

describe("buildConfigPayload", () => {
  it("wraps provider, model, and key into the PUT /v1/app/config llm shape", () => {
    expect(
      buildConfigPayload({ provider: "openai", model: "gpt-5.5", apiKey: "  sk-abc  " }),
    ).toEqual({
      llm: { provider: "openai", model: "gpt-5.5", apiKey: "sk-abc" },
    });
  });
});

describe("resolveInitialProvider", () => {
  it("returns null when no providers are reported", () => {
    expect(resolveInitialProvider([])).toBeNull();
  });

  it("returns null when only unknown providers are reported", () => {
    expect(resolveInitialProvider(["google", "cohere"])).toBeNull();
  });

  it("returns the first allowed provider from a mixed list", () => {
    expect(resolveInitialProvider(["google", "openai", "anthropic"])).toBe("openai");
  });

  it("returns the first reported provider in the normal case", () => {
    expect(resolveInitialProvider(["anthropic", "openai"])).toBe("anthropic");
  });
});

describe("applyProviderChange", () => {
  it("resets the model to the next provider's default and clears custom mode", () => {
    const draft = {
      provider: "anthropic",
      apiKey: "sk-123",
      model: "my-custom-model",
      customModel: true,
    };
    const next = applyProviderChange(draft, "openai");
    expect(next.provider).toBe("openai");
    expect(next.model).toBe(defaultModelForProvider("openai"));
    expect(next.customModel).toBe(false);
    // The key is preserved across a provider change.
    expect(next.apiKey).toBe("sk-123");
  });

  it("ignores an unsupported provider", () => {
    const draft = {
      provider: "anthropic",
      apiKey: "sk-123",
      model: "claude-sonnet-4-6",
      customModel: false,
    };
    expect(applyProviderChange(draft, "google")).toEqual(draft);
  });
});

describe("resolveSubmitModel", () => {
  it("carries the typed id on the custom path", () => {
    expect(
      resolveSubmitModel({
        provider: "anthropic",
        apiKey: "sk",
        model: "accounts/fireworks/models/x",
        customModel: true,
      }),
    ).toBe("accounts/fireworks/models/x");
  });

  it("carries the preset id on the preset path", () => {
    expect(
      resolveSubmitModel({
        provider: "anthropic",
        apiKey: "sk",
        model: "claude-sonnet-4-6",
        customModel: false,
      }),
    ).toBe("claude-sonnet-4-6");
  });
});

describe("submitProviderConfig", () => {
  const draft = {
    provider: "openai",
    apiKey: "  sk-abc  ",
    model: "gpt-5.5",
    customModel: false,
  };

  it("PUTs /v1/app/config and returns ok on 200", async () => {
    const agentFetch = vi.fn(async () => jsonResponse({ ok: true }, 200));
    const result = await submitProviderConfig(agentFetch, draft);
    expect(result).toEqual({ ok: true });
    expect(agentFetch).toHaveBeenCalledWith(
      "/v1/app/config",
      expect.objectContaining({ method: "PUT" }),
    );
    const init = agentFetch.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({
      llm: { provider: "openai", model: "gpt-5.5", apiKey: "sk-abc" },
    });
  });

  it("surfaces the server error string on a 400", async () => {
    const agentFetch = vi.fn(async () =>
      jsonResponse({ error: "unsupported_provider" }, 400),
    );
    const result = await submitProviderConfig(agentFetch, draft);
    expect(result).toEqual({ ok: false, error: "unsupported_provider" });
  });

  it("falls back to a generic message when the error body is non-JSON/empty", async () => {
    const agentFetch = vi.fn(
      async () => new Response("not json", { status: 500 }),
    );
    const result = await submitProviderConfig(agentFetch, draft);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.length).toBeGreaterThan(0);
      expect(result.error).not.toBe("");
    }
  });
});
