/**
 * loadAndRegisterUserTools.test — integration with registry + config.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

import { ToolRegistry } from "./ToolRegistry.js";
import { loadAndRegisterUserTools } from "./loadAndRegisterUserTools.js";
import type { MagiConfigData } from "../config/MagiConfig.js";
import type { Tool } from "../Tool.js";

function makeDummyTool(name: string, permission = "read"): Tool {
  return {
    name,
    description: `${name} tool`,
    permission: permission as Tool["permission"],
    inputSchema: { type: "object", properties: {}, required: [] },
    async execute() {
      return { status: "ok", durationMs: 0 };
    },
  };
}

function makeConfig(
  overrides?: Partial<MagiConfigData["tools"]>,
): MagiConfigData {
  return {
    hooks: {
      disable_builtin: [],
      directory: "./hooks",
      global_directory: "~/.magi/hooks",
      overrides: {},
    },
    tools: {
      disable_builtin: [],
      directory: "./nonexistent-tools",
      global_directory: "/tmp/nonexistent-global-tools",
      packages: [],
      overrides: {},
      ...overrides,
    },
    classifier: {
      custom_dimensions: {},
    },
  };
}

describe("loadAndRegisterUserTools", () => {
  it("returns empty result when no tools found", async () => {
    const registry = new ToolRegistry();
    const config = makeConfig();

    const result = await loadAndRegisterUserTools(registry, config);

    expect(result.registered).toBe(0);
    expect(result.skipped).toHaveLength(0);
  });

  it("disable_builtin removes tools from registry", async () => {
    const registry = new ToolRegistry();
    registry.register(makeDummyTool("Bash"));
    registry.register(makeDummyTool("Read"));

    const config = makeConfig({ disable_builtin: ["Bash"] });

    await loadAndRegisterUserTools(registry, config);

    expect(registry.resolve("Bash")).toBeNull();
    expect(registry.resolve("Read")).not.toBeNull();
  });

  it("disable_builtin handles non-existent tools gracefully", async () => {
    const registry = new ToolRegistry();
    const config = makeConfig({
      disable_builtin: ["NonExistentTool"],
    });

    // Should not throw
    const result = await loadAndRegisterUserTools(registry, config);
    expect(result.registered).toBe(0);
  });
});
