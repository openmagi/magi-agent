import { describe, it, expect } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { CompactionConfig, CompactionResult } from "./CompactionEngine.js";
import { HipocampusService } from "./HipocampusService.js";

class FakeQmdManager {
  ready = true;
  startCalls = 0;
  reindexCalls = 0;
  searchCalls: Array<{
    query: string;
    opts?: { collection?: string; limit?: number; minScore?: number };
  }> = [];
  results = [
    { path: "memory/2026-04-25.md", content: "recent context", score: 0.9 },
  ];

  isReady(): boolean {
    return this.ready;
  }

  async start(): Promise<void> {
    this.startCalls += 1;
  }

  async search(
    query: string,
    opts?: { collection?: string; limit?: number; minScore?: number },
  ): Promise<Array<{ path: string; content: string; score: number }>> {
    this.searchCalls.push({ query, opts });
    return this.results;
  }

  async reindex(): Promise<void> {
    this.reindexCalls += 1;
  }

  async stop(): Promise<void> {
    this.ready = false;
  }
}

class FakeCompactionEngine {
  constructor(private readonly result: CompactionResult) {}

  runCalls: boolean[] = [];

  async run(force?: boolean): Promise<CompactionResult> {
    this.runCalls.push(Boolean(force));
    return this.result;
  }
}

function makeConfig(): CompactionConfig {
  return {
    cooldownHours: 3,
    rootMaxTokens: 3000,
    model: "claude-sonnet",
  };
}

describe("HipocampusService", () => {
  it("prefers memory/ROOT.md over MEMORY.md for root memory", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "hipo-root-"));
    await fs.mkdir(path.join(tmp, "memory"), { recursive: true });
    await fs.writeFile(path.join(tmp, "memory", "ROOT.md"), "root-memory", "utf8");
    await fs.writeFile(path.join(tmp, "MEMORY.md"), "legacy-memory", "utf8");

    const qmd = new FakeQmdManager();
    const service = new HipocampusService({
      workspaceRoot: tmp,
      defaultModel: "claude-sonnet",
      llm: {} as never,
      qmdManager: qmd,
      loadConfig: async () => makeConfig(),
      createCompactionEngine: () =>
        new FakeCompactionEngine({
          skipped: false,
          compacted: false,
          stats: { daily: [], weekly: [], monthly: [] },
        }) as never,
    });

    await service.start();
    const root = await service.loadRootMemory();

    expect(root).toEqual({
      path: "memory/ROOT.md",
      content: "root-memory",
      bytes: Buffer.byteLength("root-memory", "utf8"),
    });
  });

  it("falls back to MEMORY.md when memory/ROOT.md is absent", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "hipo-root-"));
    await fs.writeFile(path.join(tmp, "MEMORY.md"), "legacy-memory", "utf8");

    const service = new HipocampusService({
      workspaceRoot: tmp,
      defaultModel: "claude-sonnet",
      llm: {} as never,
      qmdManager: new FakeQmdManager() as never,
      loadConfig: async () => makeConfig(),
      createCompactionEngine: () =>
        new FakeCompactionEngine({
          skipped: false,
          compacted: false,
          stats: { daily: [], weekly: [], monthly: [] },
        }) as never,
    });

    await service.start();
    const root = await service.loadRootMemory();

    expect(root).toEqual({
      path: "MEMORY.md",
      content: "legacy-memory",
      bytes: Buffer.byteLength("legacy-memory", "utf8"),
    });
  });

  it("assembles recall with root memory and qmd results", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "hipo-recall-"));
    await fs.mkdir(path.join(tmp, "memory"), { recursive: true });
    await fs.writeFile(path.join(tmp, "memory", "ROOT.md"), "stable root context", "utf8");
    const qmd = new FakeQmdManager();
    qmd.results = [
      { path: "memory/daily/2026-04-25.md", content: "narrow result", score: 0.92 },
    ];

    const service = new HipocampusService({
      workspaceRoot: tmp,
      defaultModel: "claude-sonnet",
      llm: {} as never,
      qmdManager: qmd as never,
      loadConfig: async () => makeConfig(),
      createCompactionEngine: () =>
        new FakeCompactionEngine({
          skipped: false,
          compacted: false,
          stats: { daily: [], weekly: [], monthly: [] },
        }) as never,
    });

    await service.start();
    const recall = await service.recall("what changed?", {
      collection: "memory",
      limit: 5,
      minScore: 0.3,
    });

    expect(recall.root?.path).toBe("memory/ROOT.md");
    expect(recall.root?.content).toBe("stable root context");
    expect(recall.results).toHaveLength(1);
    expect(recall.results[0]?.path).toBe("memory/daily/2026-04-25.md");
    expect(qmd.searchCalls).toEqual([
      {
        query: "what changed?",
        opts: { collection: "memory", limit: 5, minScore: 0.3 },
      },
    ]);
  });

  it("compact() reindexes when compaction produced updates", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "hipo-compact-"));
    const qmd = new FakeQmdManager();
    const engine = new FakeCompactionEngine({
      skipped: false,
      compacted: true,
      stats: { daily: ["2026-04-25"], weekly: [], monthly: [] },
    });

    const service = new HipocampusService({
      workspaceRoot: tmp,
      defaultModel: "claude-sonnet",
      llm: {} as never,
      qmdManager: qmd as never,
      loadConfig: async () => makeConfig(),
      createCompactionEngine: () => engine as never,
    });

    await service.start();
    const result = await service.compact(true);

    expect(result.compacted).toBe(true);
    expect(engine.runCalls).toEqual([true]);
    expect(qmd.reindexCalls).toBe(1);
  });
});
