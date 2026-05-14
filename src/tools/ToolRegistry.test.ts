import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ToolRegistry } from "./ToolRegistry.js";
import type { Tool } from "../Tool.js";

async function writePromptSkill(
  skillsDir: string,
  name: string,
  body = "# Body\n",
): Promise<void> {
  const dir = path.join(skillsDir, name);
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

function makeTool(name: string, overrides?: Partial<Tool>): Tool {
  return {
    name,
    description: `Tool ${name}`,
    inputSchema: { type: "object", properties: {} },
    permission: "read",
    execute: async () => ({ status: "ok" as const, durationMs: 0 }),
    ...overrides,
  };
}

describe("ToolRegistry skill reload", () => {
  let workspaceRoot: string;
  let skillsDir: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "tool-registry-"));
    skillsDir = path.join(workspaceRoot, "skills");
    await fs.mkdir(skillsDir, { recursive: true });
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("removes stale skill tools before loading the current workspace skills", async () => {
    const registry = new ToolRegistry();
    await writePromptSkill(skillsDir, "custom-old");
    await registry.loadSkills(skillsDir, workspaceRoot);

    expect(registry.resolve("custom-old")).not.toBeNull();

    await fs.rm(path.join(skillsDir, "custom-old"), { recursive: true, force: true });
    await writePromptSkill(skillsDir, "custom-new");
    await registry.loadSkills(skillsDir, workspaceRoot);

    expect(registry.resolve("custom-old")).toBeNull();
    expect(registry.resolve("custom-new")).not.toBeNull();
  });
});

describe("ToolRegistry ToolEntry features", () => {
  it("register creates ToolEntry with enabled=true and builtin source", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("FileRead"));
    const all = registry.listAll();
    expect(all).toHaveLength(1);
    expect(all[0].name).toBe("FileRead");
    expect(all[0].enabled).toBe(true);
    expect(all[0].source).toBe("builtin");
    expect(all[0].stats.calls).toBe(0);
  });

  it("derives source from tool kind", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("skill-tool", { kind: "skill" }));
    registry.register(makeTool("ext-tool", { kind: "external" }));
    registry.register(makeTool("core-tool"));

    const all = registry.listAll();
    expect(all.find((t) => t.name === "skill-tool")?.source).toBe("skill");
    expect(all.find((t) => t.name === "ext-tool")?.source).toBe("external");
    expect(all.find((t) => t.name === "core-tool")?.source).toBe("builtin");
  });

  it("disable hides tool from resolve and list", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("FileRead"));
    expect(registry.resolve("FileRead")).not.toBeNull();
    expect(registry.list()).toHaveLength(1);

    registry.disable("FileRead");
    expect(registry.resolve("FileRead")).toBeNull();
    expect(registry.list()).toHaveLength(0);
    // Still in listAll
    expect(registry.listAll()).toHaveLength(1);
    expect(registry.listAll()[0].enabled).toBe(false);
  });

  it("enable restores disabled tool", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("FileRead"));
    registry.disable("FileRead");
    registry.enable("FileRead");

    expect(registry.resolve("FileRead")).not.toBeNull();
    expect(registry.list()).toHaveLength(1);
  });

  it("unregister removes non-builtin tools", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("ext-tool", { kind: "external" }));
    expect(registry.unregister("ext-tool")).toBe(true);
    expect(registry.listAll()).toHaveLength(0);
  });

  it("unregister refuses to remove builtin tools", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("FileRead"));
    expect(registry.unregister("FileRead")).toBe(false);
    expect(registry.listAll()).toHaveLength(1);
  });

  it("unregister returns false for unknown tools", () => {
    const registry = new ToolRegistry();
    expect(registry.unregister("nonexistent")).toBe(false);
  });

  it("disable returns false for unknown tools", () => {
    const registry = new ToolRegistry();
    expect(registry.disable("nonexistent")).toBe(false);
  });

  it("enable returns false for unknown tools", () => {
    const registry = new ToolRegistry();
    expect(registry.enable("nonexistent")).toBe(false);
  });

  it("recordExecution tracks stats", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("FileRead"));

    registry.recordExecution("FileRead", 100, "ok");
    registry.recordExecution("FileRead", 200, "ok");
    registry.recordExecution("FileRead", 300, "error");

    const stats = registry.getToolStats();
    const s = stats.get("FileRead");
    expect(s).toBeDefined();
    expect(s!.calls).toBe(3);
    expect(s!.errors).toBe(1);
    expect(s!.avgDurationMs).toBe(200);
    expect(s!.lastCallAt).toBeGreaterThan(0);
  });

  it("recordExecution ignores unknown tools", () => {
    const registry = new ToolRegistry();
    // Should not throw
    registry.recordExecution("nonexistent", 100, "ok");
  });

  it("replace preserves enabled state and stats", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("FileRead"));
    registry.recordExecution("FileRead", 100, "ok");
    registry.disable("FileRead");

    registry.replace(makeTool("FileRead", { description: "Updated" }));

    const all = registry.listAll();
    expect(all[0].enabled).toBe(false); // preserved
    expect(all[0].stats.calls).toBe(1); // preserved
    expect(all[0].description).toBe("Updated"); // new value
  });

  it("getAvailableTools excludes disabled tools", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("Read"));
    registry.register(makeTool("Write", { permission: "write" }));
    registry.disable("Read");

    const available = registry.getAvailableTools();
    expect(available).toHaveLength(1);
    expect(available[0].name).toBe("Write");
  });

  it("isToolAllowedInCurrentMode returns false for disabled tools", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("Read"));
    registry.disable("Read");
    expect(registry.isToolAllowedInCurrentMode("Read")).toBe(false);
  });

  it("listAll returns metadata with correct fields", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("MyTool", {
      kind: "external",
      dangerous: true,
      isConcurrencySafe: true,
      tags: ["test", "custom"],
    }));

    const all = registry.listAll();
    expect(all).toHaveLength(1);
    const m = all[0];
    expect(m.name).toBe("MyTool");
    expect(m.source).toBe("external");
    expect(m.dangerous).toBe(true);
    expect(m.isConcurrencySafe).toBe(true);
    expect(m.tags).toEqual(["test", "custom"]);
  });
});
