import { describe, it, expect } from "vitest";
import { makeToolSearchTool, TOOL_SEARCH_NAME } from "./ToolSearch.js";
import type { ToolRegistry } from "./ToolRegistry.js";
import type { ToolContext } from "../Tool.js";

function stubCtx(): ToolContext {
  return {
    botId: "test",
    sessionKey: "s1",
    turnId: "t1",
    workspaceRoot: "/tmp",
    abortSignal: new AbortController().signal,
    askUser: async () => ({}),
    emitProgress: () => {},
    staging: {
      stageFileWrite() {},
      stageTranscriptAppend() {},
      stageAuditEvent() {},
    },
  };
}

function makeFakeRegistry(
  tools: Array<{
    name: string;
    description: string;
    inputSchema: object;
    shouldDefer?: boolean;
  }>,
): ToolRegistry {
  const fullTools = tools.map((t) => ({
    ...t,
    permission: "read" as const,
    execute: async () => ({ status: "ok" as const, durationMs: 0 }),
  }));
  return {
    register() {},
    replace() {},
    resolve(n: string) {
      return fullTools.find((t) => t.name === n) ?? null;
    },
    list() {
      return fullTools;
    },
    async loadSkills() {
      return 0;
    },
  } as unknown as ToolRegistry;
}

describe("ToolSearch", () => {
  it("has the expected tool name", () => {
    expect(TOOL_SEARCH_NAME).toBe("ToolSearch");
  });

  it("select: returns tool_reference blocks for known tools", async () => {
    const registry = makeFakeRegistry([
      {
        name: "Browser",
        description: "Browse web",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
      {
        name: "FileRead",
        description: "Read file",
        inputSchema: { type: "object" },
      },
    ]);
    const tool = makeToolSearchTool(registry);
    const result = await tool.execute(
      { query: "select:Browser", max_results: 5 },
      stubCtx(),
    );
    expect(result.status).toBe("ok");
    expect(result.output!.tool_references).toEqual([
      { type: "tool_reference", tool_name: "Browser" },
    ]);
  });

  it("select: handles multi-select", async () => {
    const registry = makeFakeRegistry([
      {
        name: "Browser",
        description: "Browse",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
      {
        name: "CronCreate",
        description: "Cron",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
    ]);
    const tool = makeToolSearchTool(registry);
    const result = await tool.execute(
      { query: "select:Browser,CronCreate" },
      stubCtx(),
    );
    expect(result.output!.tool_references).toHaveLength(2);
  });

  it("select: case-insensitive match", async () => {
    const registry = makeFakeRegistry([
      {
        name: "Browser",
        description: "Browse",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
    ]);
    const tool = makeToolSearchTool(registry);
    const result = await tool.execute(
      { query: "select:browser" },
      stubCtx(),
    );
    expect(result.output!.tool_references[0]!.tool_name).toBe("Browser");
  });

  it("select: also finds non-deferred tools (already loaded = harmless no-op)", async () => {
    const registry = makeFakeRegistry([
      {
        name: "FileRead",
        description: "Read",
        inputSchema: { type: "object" },
      },
    ]);
    const tool = makeToolSearchTool(registry);
    const result = await tool.execute(
      { query: "select:FileRead" },
      stubCtx(),
    );
    expect(result.output!.tool_references).toHaveLength(1);
  });

  it("keyword search scores by name parts", async () => {
    const registry = makeFakeRegistry([
      {
        name: "Browser",
        description: "Full Chromium browser for web pages",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
      {
        name: "CronCreate",
        description: "Create scheduled cron jobs",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
    ]);
    const tool = makeToolSearchTool(registry);
    const result = await tool.execute(
      { query: "browser web", max_results: 5 },
      stubCtx(),
    );
    expect(result.output!.tool_references[0]!.tool_name).toBe("Browser");
  });

  it("keyword search respects max_results", async () => {
    const tools = Array.from({ length: 10 }, (_, i) => ({
      name: `Tool${i}`,
      description: `test tool ${i}`,
      inputSchema: { type: "object" as const },
      shouldDefer: true,
    }));
    const registry = makeFakeRegistry(tools);
    const tool = makeToolSearchTool(registry);
    const result = await tool.execute(
      { query: "tool", max_results: 3 },
      stubCtx(),
    );
    expect(result.output!.tool_references).toHaveLength(3);
  });

  it("keyword search with + prefix requires term in name or description", async () => {
    const registry = makeFakeRegistry([
      {
        name: "CronCreate",
        description: "Create cron",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
      {
        name: "CronDelete",
        description: "Delete cron",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
      {
        name: "Browser",
        description: "Browse web",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
    ]);
    const tool = makeToolSearchTool(registry);
    const result = await tool.execute(
      { query: "+cron create" },
      stubCtx(),
    );
    const names = result.output!.tool_references.map((r) => r.tool_name);
    expect(names).toContain("CronCreate");
    expect(names).not.toContain("Browser");
  });

  it("returns total_deferred count", async () => {
    const registry = makeFakeRegistry([
      {
        name: "A",
        description: "a",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
      {
        name: "B",
        description: "b",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
      {
        name: "C",
        description: "c",
        inputSchema: { type: "object" },
      },
    ]);
    const tool = makeToolSearchTool(registry);
    const result = await tool.execute({ query: "select:A" }, stubCtx());
    expect(result.output!.total_deferred).toBe(2);
  });

  it("keyword search returns empty for no matches", async () => {
    const registry = makeFakeRegistry([
      {
        name: "Browser",
        description: "Browse",
        inputSchema: { type: "object" },
        shouldDefer: true,
      },
    ]);
    const tool = makeToolSearchTool(registry);
    const result = await tool.execute({ query: "nonexistent xyz" }, stubCtx());
    expect(result.output!.tool_references).toHaveLength(0);
  });

  it("keyword search only searches deferred tools", async () => {
    const registry = makeFakeRegistry([
      {
        name: "FileRead",
        description: "Read files from disk",
        inputSchema: { type: "object" },
        // NOT deferred
      },
    ]);
    const tool = makeToolSearchTool(registry);
    const result = await tool.execute({ query: "read file" }, stubCtx());
    expect(result.output!.tool_references).toHaveLength(0);
  });
});
