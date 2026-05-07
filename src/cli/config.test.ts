import { describe, expect, it, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { loadConfig } from "./config.js";

describe("loadConfig server settings", () => {
  const oldServerToken = process.env.MAGI_AGENT_SERVER_TOKEN;

  afterEach(() => {
    if (oldServerToken === undefined) {
      delete process.env.MAGI_AGENT_SERVER_TOKEN;
    } else {
      process.env.MAGI_AGENT_SERVER_TOKEN = oldServerToken;
    }
  });

  it("loads a dedicated server gateway token without conflating it with the provider key", async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "magi-config-"));
    process.env.MAGI_AGENT_SERVER_TOKEN = "local-web-token";
    await fs.writeFile(
      path.join(dir, "magi-agent.yaml"),
      [
        "llm:",
        "  provider: anthropic",
        "  model: claude-sonnet-4-6",
        "  apiKey: provider-secret",
        "server:",
        "  gatewayToken: ${MAGI_AGENT_SERVER_TOKEN}",
      ].join("\n"),
      "utf8",
    );

    try {
      const config = loadConfig(dir);
      expect(config.llm.apiKey).toBe("provider-secret");
      expect(config.server?.gatewayToken).toBe("local-web-token");
    } finally {
      await fs.rm(dir, { recursive: true, force: true });
    }
  });

  it("accepts OpenAI-compatible local providers without an API key", async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "magi-config-"));
    await fs.writeFile(
      path.join(dir, "magi-agent.yaml"),
      [
        "llm:",
        "  provider: openai-compatible",
        "  model: llama3.1",
        "  baseUrl: http://127.0.0.1:11434/v1",
      ].join("\n"),
      "utf8",
    );

    try {
      const config = loadConfig(dir);
      expect(config.llm.provider).toBe("openai-compatible");
      expect(config.llm.apiKey).toBeUndefined();
      expect(config.llm.baseUrl).toBe("http://127.0.0.1:11434/v1");
    } finally {
      await fs.rm(dir, { recursive: true, force: true });
    }
  });

  it("accepts model capability overrides for local providers", async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "magi-config-"));
    await fs.writeFile(
      path.join(dir, "magi-agent.yaml"),
      [
        "llm:",
        "  provider: openai-compatible",
        "  model: llama3.1",
        "  baseUrl: http://127.0.0.1:11434/v1",
        "  capabilities:",
        "    contextWindow: 65536",
        "    maxOutputTokens: 4096",
        "    supportsThinking: false",
        "    inputUsdPerMtok: 0",
        "    outputUsdPerMtok: 0",
      ].join("\n"),
      "utf8",
    );

    try {
      const config = loadConfig(dir);
      expect(config.llm.capabilities).toMatchObject({
        contextWindow: 65_536,
        maxOutputTokens: 4096,
        supportsThinking: false,
        inputUsdPerMtok: 0,
        outputUsdPerMtok: 0,
      });
    } finally {
      await fs.rm(dir, { recursive: true, force: true });
    }
  });

  it("requires a base URL for OpenAI-compatible local providers", async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "magi-config-"));
    await fs.writeFile(
      path.join(dir, "magi-agent.yaml"),
      [
        "llm:",
        "  provider: openai-compatible",
        "  model: llama3.1",
      ].join("\n"),
      "utf8",
    );

    try {
      expect(() => loadConfig(dir)).toThrow(/llm\.baseUrl/);
    } finally {
      await fs.rm(dir, { recursive: true, force: true });
    }
  });

  it("still requires API keys for hosted providers", async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "magi-config-"));
    await fs.writeFile(
      path.join(dir, "magi-agent.yaml"),
      [
        "llm:",
        "  provider: openai",
        "  model: gpt-5.4",
      ].join("\n"),
      "utf8",
    );

    try {
      expect(() => loadConfig(dir)).toThrow(/llm\.apiKey/);
    } finally {
      await fs.rm(dir, { recursive: true, force: true });
    }
  });
});
