import { describe, expect, it } from "vitest";
import { Agent, type AgentConfig } from "./Agent.js";
import { DirectLLMClient } from "./transport/DirectLLMClient.js";

function config(overrides: Partial<AgentConfig> = {}): AgentConfig {
  return {
    botId: "bot-1",
    userId: "user-1",
    workspaceRoot: "/tmp/core-agent-router-test",
    gatewayToken: "gw_test",
    apiProxyUrl: "http://api-proxy:3001",
    chatProxyUrl: "http://chat-proxy:3002",
    redisUrl: "redis://redis:6379",
    model: "claude-sonnet-4-6",
    ...overrides,
  };
}

describe("Agent native routing", () => {
  it("does not create a router when routing is off", () => {
    const agent = new Agent(config({ routingMode: "off" }));

    expect(agent.router).toBeNull();
  });

  it("creates a router for hosted-proxy routing", () => {
    const agent = new Agent(config({
      model: "magi-smart-router/auto",
      routingMode: "hosted-proxy",
      routingProfileId: "standard",
    }));

    expect(agent.router).not.toBeNull();
  });

  it("uses direct provider transport in direct routing mode", () => {
    const agent = new Agent(config({
      model: "magi-smart-router/auto",
      routingMode: "direct",
      directProviders: {
        openai: {
          kind: "openai-compatible",
          baseUrl: "https://api.openai.com",
          apiKey: "sk-test",
        },
      },
    }));

    expect(agent.router).not.toBeNull();
    expect(agent.llm).toBeInstanceOf(DirectLLMClient);
  });

  it("registers social browser as a native core-agent tool", () => {
    const agent = new Agent(config());

    expect(agent.tools.resolve("SocialBrowser")).not.toBeNull();
  });
});
