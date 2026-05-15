import { describe, it, expect } from "vitest";
import { buildOpenclawConfig } from "./config-builder";

describe("config-builder", () => {
  it("generates smart_routing config with iblai-router provider", () => {
    const config = buildOpenclawConfig({
      modelSelection: "smart_routing",
      apiKey: "sk-ant-test-key",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
      routerPort: 8402,
    });

    expect(config.models.providers["iblai-router"]).toBeDefined();
    expect(config.models.providers["iblai-router"].baseUrl).toBe("http://127.0.0.1:8402");
    expect(config.agents.defaults.model.primary).toBe("iblai-router/auto");
  });

  it("generates single model config (haiku)", () => {
    const config = buildOpenclawConfig({
      modelSelection: "haiku",
      apiKey: "sk-ant-test-key",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    expect(config.models.providers.anthropic).toBeDefined();
    expect(config.agents.defaults.model.primary).toBe("anthropic/claude-haiku-4-5");
    expect(config.models.providers["iblai-router"]).toBeUndefined();
  });

  it("sets cacheControlTtl on all models", () => {
    const config = buildOpenclawConfig({
      modelSelection: "sonnet",
      apiKey: "sk-ant-test-key",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    const models = config.agents.defaults.models;
    Object.values(models).forEach((m) => {
      expect(m.params.cacheControlTtl).toBe("1h");
    });
  });

  it("sets heartbeat to 55m", () => {
    const config = buildOpenclawConfig({
      modelSelection: "haiku",
      apiKey: "sk-ant-test-key",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    expect(config.agents.defaults.heartbeat.every).toBe("55m");
  });

  it("sets thinkingDefault to off", () => {
    const config = buildOpenclawConfig({
      modelSelection: "haiku",
      apiKey: "sk-ant-test-key",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    expect(config.agents.defaults.thinkingDefault).toBe("off");
  });

  it("sets tools.profile to full", () => {
    const config = buildOpenclawConfig({
      modelSelection: "opus",
      apiKey: "sk-ant-test-key",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    expect(config.tools.profile).toBe("full");
    expect(config.agents.defaults.model.primary).toBe("anthropic/claude-opus-4-6");
  });

  it("uses default Anthropic baseUrl when no baseUrl provided", () => {
    const config = buildOpenclawConfig({
      modelSelection: "haiku",
      apiKey: "sk-ant-test-key",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    expect(config.models.providers.anthropic.baseUrl).toBe("https://api.anthropic.com");
  });

  it("uses custom baseUrl for proxy mode", () => {
    const proxyUrl = "http://api-proxy.clawy-system.svc.cluster.local:3001";
    const config = buildOpenclawConfig({
      modelSelection: "sonnet",
      apiKey: "gw_abc123",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
      baseUrl: proxyUrl,
    });

    expect(config.models.providers.anthropic.baseUrl).toBe(proxyUrl);
    expect(config.models.providers.anthropic.apiKey).toBe("gw_abc123");
  });

  it("passes gateway token through regardless of baseUrl", () => {
    const config = buildOpenclawConfig({
      modelSelection: "haiku",
      apiKey: "gw_deadbeef",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
      baseUrl: "http://proxy:3001",
    });

    expect(config.models.providers.anthropic.apiKey).toBe("gw_deadbeef");
  });

  it("maps gpt_5_5 to GPT-5.5 with xhigh reasoning", () => {
    const config = buildOpenclawConfig({
      modelSelection: "gpt_5_5",
      apiKey: "gw_abc",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    const models = config.models.providers.openai.models as { id: string; name: string }[];
    expect(models.some((model) => model.id === "gpt-5.5" && model.name === "GPT-5.5")).toBe(true);
    expect(config.agents.defaults.model.primary).toBe("openai/gpt-5.5");
    expect(config.agents.defaults.models["openai/gpt-5.5"]?.params).toEqual({
      maxTokens: 128000,
      cacheControlTtl: "1h",
      reasoningEffort: "xhigh",
    });
  });

  it("uses Codex OAuth provider for GPT-5.5 when tokens are available", () => {
    const config = buildOpenclawConfig({
      modelSelection: "gpt_5_5",
      apiKey: "gw_abc",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
      codexAccessToken: "codex-access",
      codexRefreshToken: "codex-refresh",
    });

    expect(config.models.providers["openai-codex"]).toBeDefined();
    const codexModels = config.models.providers["openai-codex"].models as { id: string; name: string }[];
    expect(codexModels).toContainEqual(expect.objectContaining({
      id: "gpt-5.5",
      name: "Codex (GPT-5.5)",
    }));
    expect(config.agents.defaults.model.primary).toBe("openai-codex/gpt-5.5");
    expect(config.agents.defaults.models["openai-codex/gpt-5.5"]?.params.reasoningEffort).toBe("xhigh");
  });

  it("maps gpt_5_5_pro to GPT-5.5 Pro with xhigh reasoning", () => {
    const config = buildOpenclawConfig({
      modelSelection: "gpt_5_5_pro",
      apiKey: "gw_abc",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    const models = config.models.providers.openai.models as { id: string; name: string }[];
    expect(models.some((model) => model.id === "gpt-5.5-pro" && model.name === "GPT-5.5 Pro")).toBe(true);
    expect(config.agents.defaults.model.primary).toBe("openai/gpt-5.5-pro");
    expect(config.agents.defaults.models["openai/gpt-5.5-pro"]?.params).toEqual({
      maxTokens: 128000,
      cacheControlTtl: "1h",
      reasoningEffort: "xhigh",
    });
  });

  it("exposes current platform proxy models to Standard Open Magi Router bots", () => {
    const config = buildOpenclawConfig({
      modelSelection: "clawy_smart_routing",
      routerType: "standard",
      apiKey: "gw_abc",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
      baseUrl: "http://api-proxy.clawy-system.svc.cluster.local:3001",
    });

    const openaiModels = config.models.providers.openai.models as { id: string; name: string }[];
    expect(openaiModels).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "gpt-5.5", name: "GPT-5.5" }),
      expect.objectContaining({ id: "gpt-5.5-pro", name: "GPT-5.5 Pro" }),
      expect.objectContaining({ id: "gpt-5.4-mini", name: "GPT-5.4 Mini" }),
      expect.objectContaining({ id: "gpt-5.4-nano", name: "GPT-5.4 Nano" }),
    ]));
    expect(config.models.providers.openai.baseUrl).toBe("http://api-proxy.clawy-system.svc.cluster.local:3001");
  });

  it("caps Premium Router Anthropic metadata and live context budget near provider limits", () => {
    const config = buildOpenclawConfig({
      modelSelection: "clawy_smart_routing",
      routerType: "big_dic",
      apiKey: "gw_abc",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
      baseUrl: "http://api-proxy.clawy-system.svc.cluster.local:3001",
    });

    const anthropicModels = config.models.providers.anthropic.models as { id: string; contextWindow: number }[];
    expect(anthropicModels.find((model) => model.id === "claude-sonnet-4-6")).toMatchObject({
      contextWindow: 200000,
    });
    expect(anthropicModels.find((model) => model.id === "claude-opus-4-6")).toMatchObject({
      contextWindow: 262144,
    });
    expect(config.models.providers["big-dic-router"].models[0]).toMatchObject({
      contextWindow: 262144,
      maxTokens: 16384,
    });
    expect(config.agents.defaults.contextTokens).toBe(195000);
  });

  it("routes GPT smart routing heavy tiers through GPT-5.5", () => {
    const config = buildOpenclawConfig({
      modelSelection: "gpt_smart_routing",
      apiKey: "gw_abc",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    const models = config.models.providers.openai.models as { id: string }[];
    expect(models.some((model) => model.id === "gpt-5.5")).toBe(true);
    expect(config.agents.defaults.model.fallbacks).toEqual(["openai/gpt-5.5"]);
    expect(config.agents.defaults.heartbeat.model).toBe("openai/gpt-5.5");
  });

  it("maps legacy kimi_k2_5 selection to Fireworks Kimi K2.6", () => {
    const config = buildOpenclawConfig({
      modelSelection: "kimi_k2_5",
      apiKey: "gw_abc",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    const models = config.models.providers.fireworks.models as { id: string }[];
    const kimiModel = models.find((m) => m.id === "kimi-k2p6");
    expect(kimiModel).toBeDefined();
    expect(config.agents.defaults.model.primary).toBe("fireworks/kimi-k2p6");
    expect(config.agents.defaults.models["fireworks/kimi-k2p6"]?.params.maxTokens).toBe(32768);
    expect(config.models.providers["iblai-router"]).toBeUndefined();
  });

  it("adds Fireworks models to fireworks provider for minimax_m2_7", () => {
    const config = buildOpenclawConfig({
      modelSelection: "minimax_m2_7",
      apiKey: "gw_abc",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    const models = config.models.providers.fireworks.models as { id: string }[];
    const minimaxModel = models.find((m) => m.id === "minimax-m2p7");
    expect(minimaxModel).toBeDefined();
    expect(config.agents.defaults.model.primary).toBe("fireworks/minimax-m2p7");
  });

  it("adds local provider for Mac Studio models", () => {
    const config = buildOpenclawConfig({
      modelSelection: "local_gemma_fast",
      apiKey: "gw_abc",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
      baseUrl: "http://api-proxy.clawy-system.svc.cluster.local:3001",
    });

    expect(config.models.providers.local).toMatchObject({
      baseUrl: "http://api-proxy.clawy-system.svc.cluster.local:3001/v1",
      apiKey: "gw_abc",
      api: "openai-completions",
    });
    expect(config.agents.defaults.model.primary).toBe("local/gemma-fast");
    expect(config.agents.defaults.models["local/gemma-fast"]?.params.maxTokens).toBe(8192);
    expect(config.models.providers.local.models).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "gemma-fast", name: "Gemma 4 Fast (beta)" }),
        expect.objectContaining({ id: "gemma-max", name: "Gemma 4 Max (beta)" }),
        expect.objectContaining({ id: "qwen-uncensored", name: "Qwen 3.5 Uncensored (beta)" }),
      ]),
    );
    expect(JSON.stringify(config.models.providers.local.models)).not.toContain("Mac Studio");
  });

  it("enables native commands and hides nativeSkills", () => {
    const config = buildOpenclawConfig({
      modelSelection: "haiku",
      apiKey: "sk-ant-test-key",
      botToken: "123456:ABC",
      gatewayToken: "gw-token-123",
    });

    expect(config.commands.native).toBe("auto");
    expect(config.commands.nativeSkills).toBe(false);
  });
});
