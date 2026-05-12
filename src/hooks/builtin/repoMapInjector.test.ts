import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { makeRepoMapInjectorHook, _resetRepoMapState } from "./repoMapInjector.js";
import { _clearPageRankCache } from "../../services/repomap/PageRankCache.js";
import type { HookContext } from "../types.js";

function makeCtx(overrides: Partial<HookContext> = {}): HookContext {
  return {
    botId: "test-bot",
    userId: "test-user",
    sessionKey: "test-session",
    turnId: "test-turn",
    transcript: [],
    llm: {} as HookContext["llm"],
    emit: () => {},
    log: () => {},
    ...overrides,
  } as HookContext;
}

describe("repoMapInjector", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "repomap-hook-"));
    fs.mkdirSync(path.join(tmpDir, ".core-agent"), { recursive: true });
  });

  afterEach(() => {
    _resetRepoMapState();
    _clearPageRankCache();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("has correct hook metadata", () => {
    const hook = makeRepoMapInjectorHook({ workspaceRoot: tmpDir });
    expect(hook.name).toBe("builtin:repo-map-injector");
    expect(hook.point).toBe("beforeLLMCall");
    expect(hook.priority).toBe(8);
    expect(hook.blocking).toBe(false);
  });

  it("skips when iteration > 0", async () => {
    const hook = makeRepoMapInjectorHook({ workspaceRoot: tmpDir });
    const result = await hook.handler(
      { messages: [], tools: [], system: "test", iteration: 1 },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("skips when repo_map already present in system", async () => {
    const hook = makeRepoMapInjectorHook({ workspaceRoot: tmpDir });
    const result = await hook.handler(
      { messages: [], tools: [], system: "<repo_map>existing</repo_map>", iteration: 0 },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("skips when workspace has no source files", async () => {
    fs.writeFileSync(path.join(tmpDir, "README.md"), "# hello");
    const hook = makeRepoMapInjectorHook({ workspaceRoot: tmpDir });
    const result = await hook.handler(
      { messages: [], tools: [], system: "base", iteration: 0 },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("injects repo_map fence for workspace with source files", async () => {
    fs.writeFileSync(
      path.join(tmpDir, "main.ts"),
      `export function processData(items: string[]): void {}\n` +
      `export class DataEngine { run() {} }\n`,
    );
    fs.writeFileSync(
      path.join(tmpDir, "helper.ts"),
      `import { processData } from "./main";\n` +
      `export function doWork(): void { processData([]); }\n`,
    );

    const hook = makeRepoMapInjectorHook({ workspaceRoot: tmpDir });
    const result = await hook.handler(
      { messages: [], tools: [], system: "base system", iteration: 0 },
      makeCtx(),
    );

    expect(result.action).toBe("replace");
    if (result.action === "replace") {
      const system = (result.value as { system: string }).system;
      expect(system).toContain("<repo_map>");
      expect(system).toContain("</repo_map>");
      expect(system).toContain("main.ts");
      expect(system).toContain("processData");
    }
  });

  it("extracts chatFiles from transcript tool_call entries", async () => {
    fs.writeFileSync(
      path.join(tmpDir, "important.ts"),
      `export function criticalFunction(): void {}\n`,
    );
    fs.writeFileSync(
      path.join(tmpDir, "other.ts"),
      `export function otherFunction(): void {}\n` +
      `import { criticalFunction } from "./important";\n`,
    );

    const transcript = [
      {
        kind: "tool_call" as const,
        ts: Date.now(),
        turnId: "t1",
        toolUseId: "tu1",
        name: "FileRead",
        input: { file_path: "important.ts" },
      },
    ];

    const hook = makeRepoMapInjectorHook({ workspaceRoot: tmpDir });
    const result = await hook.handler(
      { messages: [], tools: [], system: "base", iteration: 0 },
      makeCtx({ transcript }),
    );

    expect(result.action).toBe("replace");
    if (result.action === "replace") {
      const system = (result.value as { system: string }).system;
      expect(system).toContain("important.ts");
    }
  });

  it("uses in-memory PageRank cache on second call", async () => {
    fs.writeFileSync(
      path.join(tmpDir, "cached.ts"),
      `export function cachedFunc(): void {}\n`,
    );

    const hook = makeRepoMapInjectorHook({ workspaceRoot: tmpDir });
    const args = { messages: [], tools: [], system: "base", iteration: 0 };
    const ctx = makeCtx();

    const result1 = await hook.handler(args, ctx);
    expect(result1.action).toBe("replace");

    const result2 = await hook.handler(args, ctx);
    expect(result2.action).toBe("replace");
  });

  it("skips when CORE_AGENT_REPO_MAP is off", async () => {
    process.env.CORE_AGENT_REPO_MAP = "off";
    try {
      fs.writeFileSync(path.join(tmpDir, "x.ts"), "export const x = 1;");
      const hook = makeRepoMapInjectorHook({ workspaceRoot: tmpDir });
      const result = await hook.handler(
        { messages: [], tools: [], system: "base", iteration: 0 },
        makeCtx(),
      );
      expect(result).toEqual({ action: "continue" });
    } finally {
      delete process.env.CORE_AGENT_REPO_MAP;
    }
  });

  it("fail-open on workspace not found", async () => {
    const hook = makeRepoMapInjectorHook({ workspaceRoot: "/nonexistent/path" });
    const result = await hook.handler(
      { messages: [], tools: [], system: "base", iteration: 0 },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });
});
