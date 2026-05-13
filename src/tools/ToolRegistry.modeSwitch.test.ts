import { describe, expect, it, beforeEach } from "vitest";
import { ToolRegistry } from "./ToolRegistry.js";
import type { Tool, ToolResult } from "../Tool.js";

function stubTool(overrides: Partial<Tool> & { name: string }): Tool {
  return {
    description: `stub ${overrides.name}`,
    inputSchema: { type: "object" },
    permission: "read",
    execute: async (): Promise<ToolResult> => ({
      status: "ok",
      durationMs: 0,
    }),
    ...overrides,
  };
}

describe("ToolRegistry mode switching", () => {
  let registry: ToolRegistry;

  beforeEach(() => {
    registry = new ToolRegistry();
    registry.register(
      stubTool({ name: "FileRead", permission: "read", availableInModes: ["plan", "act"] }),
    );
    registry.register(
      stubTool({ name: "Glob", permission: "read", availableInModes: ["plan", "act"] }),
    );
    registry.register(
      stubTool({ name: "Grep", permission: "read", availableInModes: ["plan", "act"] }),
    );
    registry.register(
      stubTool({ name: "FileWrite", permission: "write", availableInModes: ["act"] }),
    );
    registry.register(
      stubTool({ name: "FileEdit", permission: "write", availableInModes: ["act"] }),
    );
    registry.register(
      stubTool({ name: "Bash", permission: "execute", availableInModes: ["act"] }),
    );
    registry.register(
      stubTool({ name: "plan_mode_respond", permission: "meta", availableInModes: ["plan"] }),
    );
    registry.register(
      stubTool({ name: "WebSearch", permission: "net" }),
    );
  });

  it("defaults to act mode", () => {
    expect(registry.getMode()).toBe("act");
  });

  it("setMode(plan) filters out act-only tools from getAvailableTools()", () => {
    registry.setMode("plan");
    const available = registry.getAvailableTools();
    const names = available.map((t) => t.name);
    expect(names).toContain("FileRead");
    expect(names).toContain("Glob");
    expect(names).toContain("Grep");
    expect(names).toContain("plan_mode_respond");
    expect(names).toContain("WebSearch");
    expect(names).not.toContain("FileWrite");
    expect(names).not.toContain("FileEdit");
    expect(names).not.toContain("Bash");
  });

  it("setMode(act) returns all tools except plan-only tools", () => {
    registry.setMode("act");
    const available = registry.getAvailableTools();
    const names = available.map((t) => t.name);
    expect(names).toContain("FileRead");
    expect(names).toContain("FileWrite");
    expect(names).toContain("FileEdit");
    expect(names).toContain("Bash");
    expect(names).toContain("WebSearch");
    expect(names).not.toContain("plan_mode_respond");
  });

  it("net/read tools without availableInModes are available in both modes", () => {
    registry.setMode("plan");
    expect(registry.getAvailableTools().map((t) => t.name)).toContain("WebSearch");
    registry.setMode("act");
    expect(registry.getAvailableTools().map((t) => t.name)).toContain("WebSearch");
  });

  it("write/execute tools without availableInModes infer act-only", () => {
    registry.register(stubTool({ name: "ImplicitWrite", permission: "write" }));
    registry.register(stubTool({ name: "ImplicitExec", permission: "execute" }));
    registry.setMode("plan");
    const names = registry.getAvailableTools().map((t) => t.name);
    expect(names).not.toContain("ImplicitWrite");
    expect(names).not.toContain("ImplicitExec");
    registry.setMode("act");
    const actNames = registry.getAvailableTools().map((t) => t.name);
    expect(actNames).toContain("ImplicitWrite");
    expect(actNames).toContain("ImplicitExec");
  });

  it("mutatesWorkspace tools without availableInModes infer act-only", () => {
    registry.register(stubTool({ name: "MutatingRead", permission: "read", mutatesWorkspace: true }));
    registry.setMode("plan");
    expect(registry.getAvailableTools().map((t) => t.name)).not.toContain("MutatingRead");
    registry.setMode("act");
    expect(registry.getAvailableTools().map((t) => t.name)).toContain("MutatingRead");
  });

  it("list() always returns all registered tools regardless of mode", () => {
    registry.setMode("plan");
    const all = registry.list();
    expect(all.length).toBe(8);
    expect(all.map((t) => t.name)).toContain("FileWrite");
  });

  it("resolve() works regardless of mode", () => {
    registry.setMode("plan");
    expect(registry.resolve("FileWrite")).not.toBeNull();
    expect(registry.resolve("plan_mode_respond")).not.toBeNull();
  });
});

describe("ToolRegistry strict plan mode enforcement", () => {
  let registry: ToolRegistry;

  beforeEach(() => {
    registry = new ToolRegistry();
    registry.register(
      stubTool({ name: "FileRead", permission: "read", availableInModes: ["plan", "act"] }),
    );
    registry.register(
      stubTool({ name: "FileEdit", permission: "write", availableInModes: ["act"] }),
    );
  });

  it("isToolAllowedInCurrentMode returns false for act-only tool in plan mode", () => {
    registry.setMode("plan");
    expect(registry.isToolAllowedInCurrentMode("FileEdit")).toBe(false);
  });

  it("isToolAllowedInCurrentMode returns true for plan-allowed tool in plan mode", () => {
    registry.setMode("plan");
    expect(registry.isToolAllowedInCurrentMode("FileRead")).toBe(true);
  });

  it("isToolAllowedInCurrentMode returns true for any registered tool in act mode", () => {
    registry.setMode("act");
    expect(registry.isToolAllowedInCurrentMode("FileEdit")).toBe(true);
    expect(registry.isToolAllowedInCurrentMode("FileRead")).toBe(true);
  });

  it("isToolAllowedInCurrentMode returns false for unregistered tool", () => {
    expect(registry.isToolAllowedInCurrentMode("NonExistent")).toBe(false);
  });
});
