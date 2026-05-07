import { describe, expect, it } from "vitest";
import type { AgentConfig } from "../Agent.js";
import type { MagiAgentConfig } from "./config.js";
import { resolveHttpBearerToken } from "./serve.js";

function baseConfig(): MagiAgentConfig {
  return {
    llm: {
      provider: "anthropic",
      model: "claude-sonnet-4-6",
      apiKey: "provider-secret",
    },
  };
}

function baseAgentConfig(): AgentConfig {
  return {
    botId: "cli-serve",
    userId: "cli-user",
    workspaceRoot: "/tmp/magi-agent",
    gatewayToken: "provider-secret",
    apiProxyUrl: "https://api.anthropic.com",
    model: "claude-sonnet-4-6",
  };
}

describe("resolveHttpBearerToken", () => {
  it("prefers server.gatewayToken over the provider API key", () => {
    const config = {
      ...baseConfig(),
      server: { gatewayToken: "local-web-token" },
    };

    expect(resolveHttpBearerToken(config, baseAgentConfig())).toBe(
      "local-web-token",
    );
  });

  it("preserves legacy config behavior when no server token is configured", () => {
    expect(resolveHttpBearerToken(baseConfig(), baseAgentConfig())).toBe(
      "provider-secret",
    );
  });

  it("rejects an explicitly empty server.gatewayToken", () => {
    const config = {
      ...baseConfig(),
      server: { gatewayToken: "" },
    };

    expect(() => resolveHttpBearerToken(config, baseAgentConfig())).toThrow(
      /server.gatewayToken/,
    );
  });
});
