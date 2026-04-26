import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Agent } from "./Agent.js";
import type { OutboundMessage } from "./channels/ChannelAdapter.js";

class FakeWebAppAdapter {
  readonly sent: OutboundMessage[] = [];

  async send(msg: OutboundMessage): Promise<void> {
    this.sent.push(msg);
  }
}

describe("Agent background task delivery", () => {
  let workspaceRoot: string;
  let agent: Agent;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "agent-bg-delivery-"));
    agent = new Agent({
      botId: "bot-1",
      userId: "user-1",
      workspaceRoot,
      gatewayToken: "tok",
      apiProxyUrl: "http://proxy",
      chatProxyUrl: "http://chat",
      redisUrl: "redis://r",
      model: "claude-haiku",
    });
  });

  afterEach(async () => {
    await agent.stop();
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("pushes completed background results to the original app channel", async () => {
    const webAppAdapter = new FakeWebAppAdapter();
    (agent as unknown as { webAppAdapter: FakeWebAppAdapter }).webAppAdapter =
      webAppAdapter;
    await agent.getOrCreateSession("agent:main:app:general", {
      type: "app",
      channelId: "general",
    });

    const delivered = await agent.deliverBackgroundTaskResult({
      sessionKey: "agent:main:app:general",
      taskId: "task_123",
      status: "completed",
      finalText: "The background result is ready.",
    });

    expect(delivered).toBe(true);
    expect(webAppAdapter.sent).toEqual([
      { chatId: "general", text: "The background result is ready." },
    ]);
  });
});
