import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { loadAgentConfigFile } from "./AgentConfigFile.js";

describe("loadAgentConfigFile", () => {
  let workspaceRoot: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "agent-config-"));
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("returns defaults when no file exists", async () => {
    const { config, warnings } = await loadAgentConfigFile(workspaceRoot);
    expect(warnings).toHaveLength(0);
    expect(config.tools.disabled).toEqual([]);
    expect(config.tools.config).toEqual({});
    expect(config.tools.externalDirs).toEqual([]);
    expect(config.tools.trustedDirs).toEqual([]);
    expect(config.tools.maxToolsPerTurn).toBe(50);
  });

  it("parses disabled tools", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "tools:\n  disabled:\n    - Bash\n    - Browser\n",
    );
    const { config, warnings } = await loadAgentConfigFile(workspaceRoot);
    expect(warnings).toHaveLength(0);
    expect(config.tools.disabled).toEqual(["Bash", "Browser"]);
  });

  it("parses tool config", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "tools:\n  config:\n    Bash:\n      timeout: 30000\n",
    );
    const { config, warnings } = await loadAgentConfigFile(workspaceRoot);
    expect(warnings).toHaveLength(0);
    expect(config.tools.config.Bash).toEqual({ timeout: 30000 });
  });

  it("parses externalDirs (camelCase)", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "tools:\n  externalDirs:\n    - ./my-tools\n    - /abs/tools\n",
    );
    const { config } = await loadAgentConfigFile(workspaceRoot);
    expect(config.tools.externalDirs).toEqual(["./my-tools", "/abs/tools"]);
  });

  it("parses external_dirs (snake_case)", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "tools:\n  external_dirs:\n    - ./snake-tools\n",
    );
    const { config } = await loadAgentConfigFile(workspaceRoot);
    expect(config.tools.externalDirs).toEqual(["./snake-tools"]);
  });

  it("parses trustedDirs", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "tools:\n  trustedDirs:\n    - /trusted/a\n",
    );
    const { config } = await loadAgentConfigFile(workspaceRoot);
    expect(config.tools.trustedDirs).toEqual(["/trusted/a"]);
  });

  it("handles invalid YAML gracefully", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "tools:\n  disabled:\n    - ok\n  bad: [\n",
    );
    const { config, warnings } = await loadAgentConfigFile(workspaceRoot);
    expect(warnings.length).toBeGreaterThan(0);
    expect(warnings[0]).toContain("failed to parse");
    // Returns defaults
    expect(config.tools.disabled).toEqual([]);
  });

  it("clamps maxToolsPerTurn to valid range", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "tools:\n  maxToolsPerTurn: 200\n",
    );
    const { config } = await loadAgentConfigFile(workspaceRoot);
    expect(config.tools.maxToolsPerTurn).toBe(100);
  });

  it("clamps maxToolsPerTurn minimum to 1", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "tools:\n  maxToolsPerTurn: -5\n",
    );
    const { config } = await loadAgentConfigFile(workspaceRoot);
    expect(config.tools.maxToolsPerTurn).toBe(1);
  });

  it("accepts max_tools_per_turn snake_case", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "tools:\n  max_tools_per_turn: 25\n",
    );
    const { config } = await loadAgentConfigFile(workspaceRoot);
    expect(config.tools.maxToolsPerTurn).toBe(25);
  });

  it("filters empty strings from disabled list", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "tools:\n  disabled:\n    - Bash\n    - ''\n    - '  '\n    - Browser\n",
    );
    const { config } = await loadAgentConfigFile(workspaceRoot);
    expect(config.tools.disabled).toEqual(["Bash", "Browser"]);
  });

  it("ignores non-object values in tool config", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "tools:\n  config:\n    Bash:\n      timeout: 30000\n    BadTool: just-a-string\n",
    );
    const { config } = await loadAgentConfigFile(workspaceRoot);
    expect(config.tools.config.Bash).toEqual({ timeout: 30000 });
    expect(config.tools.config.BadTool).toBeUndefined();
  });
});
