import { afterEach, describe, expect, it, vi } from "vitest";
import { getCapability, resetCustomModelCapabilitiesForTests } from "../llm/modelCapabilities.js";
import { loadFromConfig, loadRuntimeEnv } from "./RuntimeEnv.js";

const REQUIRED_ENV = {
  BOT_ID: "bot",
  USER_ID: "user",
  GATEWAY_TOKEN: "gateway",
  CORE_AGENT_API_PROXY_URL: "http://api-proxy",
  CORE_AGENT_CHAT_PROXY_URL: "http://chat-proxy",
  CORE_AGENT_REDIS_URL: "redis://localhost:6379",
};

function setRequiredEnv(): void {
  for (const [key, value] of Object.entries(REQUIRED_ENV)) {
    vi.stubEnv(key, value);
  }
}

describe("loadRuntimeEnv", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    resetCustomModelCapabilitiesForTests();
  });

  it("uses workspace-bypass as the default env permission mode", () => {
    setRequiredEnv();

    const env = loadRuntimeEnv();

    expect(env.agentConfig.defaultPermissionMode).toBe("workspace-bypass");
  });

  it("accepts CORE_AGENT_PERMISSION_MODE as an explicit override", () => {
    setRequiredEnv();
    vi.stubEnv("CORE_AGENT_PERMISSION_MODE", "auto");

    const env = loadRuntimeEnv();

    expect(env.agentConfig.defaultPermissionMode).toBe("auto");
  });
});

describe("loadRuntimeEnv routing", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    resetCustomModelCapabilitiesForTests();
  });

  it("defaults routing off for normal single-model bots", () => {
    setRequiredEnv();
    vi.stubEnv("CORE_AGENT_MODEL", "claude-sonnet-4-6");

    const env = loadRuntimeEnv();

    expect(env.agentConfig.routingMode).toBe("off");
    expect(env.agentConfig.routingProfileId).toBe("standard");
  });

  it("defaults router keyword bots to hosted-proxy routing", () => {
    setRequiredEnv();
    vi.stubEnv("CORE_AGENT_MODEL", "magi-smart-router/auto");

    const env = loadRuntimeEnv();

    expect(env.agentConfig.routingMode).toBe("hosted-proxy");
    expect(env.agentConfig.routingProfileId).toBe("standard");
  });

  it("accepts explicit direct routing mode for standalone deployments", () => {
    setRequiredEnv();
    vi.stubEnv("CORE_AGENT_MODEL", "magi-smart-router/auto");
    vi.stubEnv("CORE_AGENT_ROUTING_MODE", "direct");
    vi.stubEnv("CORE_AGENT_ROUTING_PROFILE", "anthropic_only");
    vi.stubEnv("ANTHROPIC_API_KEY", "sk-ant-test");

    const env = loadRuntimeEnv();

    expect(env.agentConfig.routingMode).toBe("direct");
    expect(env.agentConfig.routingProfileId).toBe("anthropic_only");
    expect(env.agentConfig.directProviders?.anthropic).toMatchObject({
      kind: "anthropic",
      baseUrl: "https://api.anthropic.com",
      apiKey: "sk-ant-test",
    });
  });

  it("configures local OpenAI-compatible providers in direct mode without API keys", () => {
    setRequiredEnv();
    vi.stubEnv("CORE_AGENT_MODEL", "magi-smart-router/auto");
    vi.stubEnv("CORE_AGENT_ROUTING_MODE", "direct");
    vi.stubEnv("OLLAMA_BASE_URL", "http://ollama.local:11434/v1");
    vi.stubEnv("LM_STUDIO_BASE_URL", "http://lmstudio.local:1234/v1");
    vi.stubEnv("VLLM_BASE_URL", "http://gpu.local:8000/v1");
    vi.stubEnv("TGI_BASE_URL", "http://tgi.local:8080/v1");
    vi.stubEnv("OPENROUTER_API_KEY", "sk-or-test");

    const env = loadRuntimeEnv();

    expect(env.agentConfig.directProviders?.ollama).toEqual({
      kind: "openai-compatible",
      baseUrl: "http://ollama.local:11434/v1",
      apiKey: "",
    });
    expect(env.agentConfig.directProviders?.local).toEqual({
      kind: "openai-compatible",
      baseUrl: "http://lmstudio.local:1234/v1",
      apiKey: "",
    });
    expect(env.agentConfig.directProviders?.vllm).toEqual({
      kind: "openai-compatible",
      baseUrl: "http://gpu.local:8000/v1",
      apiKey: "",
    });
    expect(env.agentConfig.directProviders?.tgi).toEqual({
      kind: "openai-compatible",
      baseUrl: "http://tgi.local:8080/v1",
      apiKey: "",
    });
    expect(env.agentConfig.directProviders?.openrouter).toEqual({
      kind: "openai-compatible",
      baseUrl: "https://openrouter.ai/api/v1",
      apiKey: "sk-or-test",
    });
  });
});

describe("loadFromConfig model capabilities", () => {
  afterEach(() => {
    resetCustomModelCapabilitiesForTests();
  });

  it("registers local model capabilities from magi-agent.yaml config", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    loadFromConfig({
      llm: {
        provider: "openai-compatible",
        model: "llama3.1",
        baseUrl: "http://127.0.0.1:11434/v1",
        capabilities: {
          contextWindow: 65_536,
          maxOutputTokens: 4096,
          supportsThinking: false,
          inputUsdPerMtok: 0,
          outputUsdPerMtok: 0,
        },
      },
    });

    expect(getCapability("llama3.1")).toMatchObject({
      contextWindow: 65_536,
      maxOutputTokens: 4096,
    });
    expect(warn).not.toHaveBeenCalled();
  });
});
