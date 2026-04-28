import { describe, expect, it } from "vitest";
import type { Tool, ToolRegistry } from "../Tool.js";
import { buildUnknownToolMessage, decideToolAccess } from "./ToolArbiter.js";

function tool(name: string): Tool {
  return {
    name,
    description: name,
    inputSchema: { type: "object" },
    permission: "read",
    execute: async () => ({ status: "ok", durationMs: 1 }),
  };
}

function registry(tools: Tool[]): ToolRegistry {
  return {
    register: () => {},
    resolve: (name) => tools.find((t) => t.name === name) ?? null,
    list: () => tools,
    loadSkills: async () => 0,
  };
}

describe("ToolArbiter", () => {
  it("allows tools that resolve and are exposed", () => {
    const reg = registry([tool("FileRead"), tool("Bash")]);
    const decision = decideToolAccess({
      registry: reg,
      toolName: "FileRead",
      exposedToolNames: ["FileRead"],
    });

    expect(decision.allowed).toBe(true);
    if (decision.allowed) expect(decision.tool.name).toBe("FileRead");
  });

  it("denies registry tools hidden by the exposed-tool allowlist", () => {
    const reg = registry([tool("FileRead"), tool("Bash")]);
    const decision = decideToolAccess({
      registry: reg,
      toolName: "Bash",
      exposedToolNames: ["FileRead"],
    });

    expect(decision.allowed).toBe(false);
    if (!decision.allowed) {
      expect(decision.reason).toBe("not_exposed");
      expect(decision.availableNames).toEqual(["FileRead"]);
      expect(decision.message).toContain("Unknown tool: Bash");
      expect(decision.message).toContain("Available tools: FileRead");
    }
  });

  it("denies unknown tools and reports available registry tools", () => {
    const reg = registry([tool("FileRead"), tool("Bash")]);
    const decision = decideToolAccess({ registry: reg, toolName: "Nope" });

    expect(decision.allowed).toBe(false);
    if (!decision.allowed) {
      expect(decision.reason).toBe("unknown_tool");
      expect(decision.availableNames).toEqual(["Bash", "FileRead"]);
      expect(decision.message).toContain("Unknown tool: Nope");
    }
  });

  it("caps unknown-tool hints to twenty names", () => {
    const names = Array.from({ length: 22 }, (_, i) => `T${String(i).padStart(2, "0")}`);
    const message = buildUnknownToolMessage("Missing", names);
    expect(message).toContain("(+2 more)");
  });
});
