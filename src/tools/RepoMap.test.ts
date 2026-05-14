import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeRepoMapTool } from "./RepoMap.js";

function toolContext(workspaceRoot: string): ToolContext {
  return {
    botId: "bot",
    sessionKey: "session",
    turnId: "turn-1",
    workspaceRoot,
    askUser: async () => ({}),
    emitProgress: () => {},
    abortSignal: new AbortController().signal,
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

describe("RepoMap", () => {
  let tmpDir: string;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "repomap-test-"));
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  it("returns directory tree for a temp workspace", async () => {
    // Create a simple project structure
    await fs.mkdir(path.join(tmpDir, "src"), { recursive: true });
    await fs.writeFile(
      path.join(tmpDir, "src", "index.ts"),
      'export function main(): void {\n  console.log("hello");\n}\n',
    );
    await fs.writeFile(
      path.join(tmpDir, "src", "util.ts"),
      "export const VERSION = 1;\n",
    );
    await fs.writeFile(
      path.join(tmpDir, "package.json"),
      '{"name": "test"}\n',
    );

    const tool = makeRepoMapTool(tmpDir);
    const result = await tool.execute({}, toolContext(tmpDir));

    expect(result.status).toBe("ok");
    expect(result.output.tree.length).toBeGreaterThan(0);
    // Should have the src directory and files
    expect(result.output.tree.some((line) => line.includes("src/"))).toBe(true);
    expect(result.output.tree.some((line) => line.includes("index.ts"))).toBe(true);
    expect(result.output.tree.some((line) => line.includes("util.ts"))).toBe(true);
    // Should extract symbols
    expect(result.output.symbols.length).toBeGreaterThan(0);
    const indexSymbols = result.output.symbols.find((s) => s.file.includes("index.ts"));
    expect(indexSymbols).toBeDefined();
    expect(indexSymbols!.definitions.some((d) => d.includes("main"))).toBe(true);
  });

  it("respects maxFiles limit", async () => {
    // Create many source files
    await fs.mkdir(path.join(tmpDir, "src"), { recursive: true });
    for (let i = 0; i < 10; i++) {
      await fs.writeFile(
        path.join(tmpDir, "src", `file${i}.ts`),
        `export function fn${i}(): void {}\n`,
      );
    }

    const tool = makeRepoMapTool(tmpDir);
    const result = await tool.execute({ maxFiles: 3 }, toolContext(tmpDir));

    expect(result.status).toBe("ok");
    // symbols extracted from at most 3 files
    expect(result.output.symbols.length).toBeLessThanOrEqual(3);
    expect(result.output.truncated).toBe(true);
  });

  it("respects maxDepth limit", async () => {
    // Create nested directories: depth 0, 1, 2, 3
    const deep = path.join(tmpDir, "a", "b", "c", "d");
    await fs.mkdir(deep, { recursive: true });
    await fs.writeFile(path.join(tmpDir, "a", "top.ts"), "export const A = 1;\n");
    await fs.writeFile(path.join(tmpDir, "a", "b", "mid.ts"), "export const B = 2;\n");
    await fs.writeFile(path.join(tmpDir, "a", "b", "c", "low.ts"), "export const C = 3;\n");
    await fs.writeFile(path.join(deep, "deep.ts"), "export const D = 4;\n");

    const tool = makeRepoMapTool(tmpDir);
    const result = await tool.execute({ maxDepth: 2 }, toolContext(tmpDir));

    expect(result.status).toBe("ok");
    // Should see top.ts (depth 1) and mid.ts (depth 2) but not low.ts (depth 3) or deep.ts (depth 4)
    expect(result.output.tree.some((line) => line.includes("top.ts"))).toBe(true);
    expect(result.output.tree.some((line) => line.includes("mid.ts"))).toBe(true);
    expect(result.output.tree.some((line) => line.includes("low.ts"))).toBe(false);
    expect(result.output.tree.some((line) => line.includes("deep.ts"))).toBe(false);
  });
});
