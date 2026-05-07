import { afterEach, describe, expect, it, vi } from "vitest";
import { loadRuntimeEnv } from "./RuntimeEnv.js";

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
  });

  it("preserves hosted UX by making bypass an explicit env-mode default", () => {
    setRequiredEnv();

    const env = loadRuntimeEnv();

    expect(env.agentConfig.defaultPermissionMode).toBe("bypass");
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
});
