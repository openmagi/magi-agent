import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { Agent } from "./Agent.js";

const roots: string[] = [];

async function makeWorkspace(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "agent-tools-"));
  roots.push(root);
  return root;
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("Agent built-in tools", () => {
  it("registers documented native web tools by default", async () => {
    const workspaceRoot = await makeWorkspace();
    const agent = new Agent({
      botId: "bot-1",
      userId: "user-1",
      workspaceRoot,
      gatewayToken: "tok",
      apiProxyUrl: "http://proxy",
      chatProxyUrl: "http://chat",
      redisUrl: "redis://r",
      model: "claude-haiku",
    });

    try {
      expect(agent.tools.resolve("WebSearch")).not.toBeNull();
      expect(agent.tools.resolve("WebFetch")).not.toBeNull();
    } finally {
      await agent.stop();
    }
  });
});
