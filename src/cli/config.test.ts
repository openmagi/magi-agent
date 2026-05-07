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
});
