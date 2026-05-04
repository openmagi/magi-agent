import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Agent, type AgentConfig } from "./Agent.js";

function config(overrides: Partial<AgentConfig> = {}): AgentConfig {
  return {
    botId: "bot-superpowers",
    userId: "user-superpowers",
    workspaceRoot: "/tmp/core-agent-superpowers-test",
    gatewayToken: "gw_test",
    apiProxyUrl: "http://api-proxy:3001",
    chatProxyUrl: "http://chat-proxy:3002",
    redisUrl: "redis://redis:6379",
    model: "claude-sonnet-4-6",
    ...overrides,
  };
}

async function writePromptSkill(
  root: string,
  name: string,
  body = "# Body\n",
): Promise<void> {
  const dir = path.join(root, name);
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(
    path.join(dir, "SKILL.md"),
    [
      "---",
      `name: ${name}`,
      `description: Use this skill to ${name.replace(/-/g, " ")}.`,
      "kind: prompt",
      "---",
      "",
      body,
    ].join("\n"),
    "utf8",
  );
}

describe("Agent bundled superpowers loading", () => {
  let root: string;
  let workspaceRoot: string;
  let workspaceSkillsDir: string;
  let bundledSuperpowersDir: string;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "agent-superpowers-"));
    workspaceRoot = path.join(root, "workspace");
    workspaceSkillsDir = path.join(workspaceRoot, "skills");
    bundledSuperpowersDir = path.join(root, "bundled-superpowers");
    await fs.mkdir(workspaceSkillsDir, { recursive: true });
    await fs.mkdir(bundledSuperpowersDir, { recursive: true });
  });

  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("loads bundled superpowers as skill tools alongside workspace skills", async () => {
    await writePromptSkill(workspaceSkillsDir, "domain-helper");
    await writePromptSkill(
      bundledSuperpowersDir,
      "using-superpowers",
      "# Using Superpowers\n\nAlways check relevant skills first.",
    );

    const agent = new Agent(
      config({
        workspaceRoot,
        superpowersSkillsDir: bundledSuperpowersDir,
      }),
    );

    const result = await agent.reloadWorkspaceSkills();

    expect(result.loaded).toBe(2);
    expect(agent.tools.resolve("domain-helper")).not.toBeNull();
    expect(agent.tools.resolve("using-superpowers")).not.toBeNull();
  });
});
