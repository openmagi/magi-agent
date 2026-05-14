import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { extractTags, _resetParsers } from "./TagExtractor.js";
import { DependencyGraph } from "./DependencyGraph.js";
import { computePageRank } from "./PageRank.js";
import { renderRepoMap, getTokenBudget } from "./RepoMapRenderer.js";
import { TagCache } from "./TagCache.js";
import type { Tag } from "./types.js";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

// ---------- TagExtractor: TypeScript ----------

describe("TagExtractor", () => {
  describe("TypeScript", () => {
    it("extracts function definitions", async () => {
      const source = `
export function buildMessages(ctx: Context): Message[] {
  return [];
}

export async function sendNotification(user: User): Promise<void> {}
`;
      const tags = await extractTags(source, "src/builder.ts", "typescript");
      const defs = tags.filter((t) => t.kind === "def");
      expect(defs.map((d) => d.name)).toContain("buildMessages");
      expect(defs.map((d) => d.name)).toContain("sendNotification");
    });

    it("extracts class and interface definitions", async () => {
      const source = `
export class ContextEngine {
  run() {}
}

export interface ToolContext {
  workspace: Workspace;
}

type SessionId = string;
`;
      const tags = await extractTags(source, "src/engine.ts", "typescript");
      const defs = tags.filter((t) => t.kind === "def");
      const names = defs.map((d) => d.name);
      expect(names).toContain("ContextEngine");
      expect(names).toContain("ToolContext");
      expect(names).toContain("SessionId");
    });

    it("extracts const/variable definitions", async () => {
      const source = `
export const MAX_RETRY = 3;
const helperUtil = () => {};
let mutableState = false;
`;
      const tags = await extractTags(source, "src/config.ts", "typescript");
      const defs = tags.filter((t) => t.kind === "def");
      const names = defs.map((d) => d.name);
      expect(names).toContain("MAX_RETRY");
      expect(names).toContain("helperUtil");
      expect(names).toContain("mutableState");
    });

    it("extracts references to identifiers", async () => {
      const source = `
import { ContextEngine } from "./engine";
const result = ContextEngine.run(buildMessages());
`;
      const tags = await extractTags(source, "src/main.ts", "typescript");
      const refs = tags.filter((t) => t.kind === "ref");
      const names = refs.map((r) => r.name);
      expect(names).toContain("ContextEngine");
      expect(names).toContain("buildMessages");
    });

    it("ignores JS keywords", async () => {
      const source = `
if (true) {
  return undefined;
}
`;
      const tags = await extractTags(source, "src/cond.ts", "typescript");
      const names = tags.map((t) => t.name);
      expect(names).not.toContain("if");
      expect(names).not.toContain("true");
      expect(names).not.toContain("return");
      expect(names).not.toContain("undefined");
    });
  });

  // ---------- TagExtractor: JavaScript ----------

  describe("JavaScript", () => {
    it("extracts function and const definitions", async () => {
      const source = `
function processRequest(req, res) {
  return res.json({});
}

const helperFn = (x) => x * 2;
`;
      const tags = await extractTags(source, "src/handler.js", "javascript");
      const defs = tags.filter((t) => t.kind === "def");
      const names = defs.map((d) => d.name);
      expect(names).toContain("processRequest");
      expect(names).toContain("helperFn");
    });
  });

  // ---------- TagExtractor: Python ----------

  describe("Python", () => {
    it("extracts def and class definitions", async () => {
      const source = `
def calculate_score(items):
    return sum(items)

class DataProcessor:
    def process(self, data):
        return data
`;
      const tags = await extractTags(source, "src/processor.py", "python");
      const defs = tags.filter((t) => t.kind === "def");
      const names = defs.map((d) => d.name);
      expect(names).toContain("calculate_score");
      expect(names).toContain("DataProcessor");
      expect(names).toContain("process");
    });

    it("extracts references", async () => {
      const source = `
from processor import DataProcessor
result = DataProcessor().process(calculate_score([1,2,3]))
`;
      const tags = await extractTags(source, "src/main.py", "python");
      const refs = tags.filter((t) => t.kind === "ref");
      const names = refs.map((r) => r.name);
      expect(names).toContain("DataProcessor");
      expect(names).toContain("calculate_score");
    });

    it("extracts top-level assignment definitions", async () => {
      const source = `
MAX_RETRIES = 5
default_config = {"timeout": 30}
`;
      const tags = await extractTags(source, "src/config.py", "python");
      const defs = tags.filter((t) => t.kind === "def");
      const names = defs.map((d) => d.name);
      expect(names).toContain("MAX_RETRIES");
      expect(names).toContain("default_config");
    });
  });
});

// ---------- DependencyGraph ----------

describe("DependencyGraph", () => {
  it("creates edge when file A references symbol defined in file B", () => {
    const tags: Tag[] = [
      { file: "src/a.ts", name: "processData", kind: "def", line: 1, language: "typescript" },
      { file: "src/b.ts", name: "processData", kind: "ref", line: 5, language: "typescript" },
    ];
    const graph = DependencyGraph.build(tags);
    const edges = graph.getEdges();
    expect(edges).toHaveLength(1);
    expect(edges[0]).toMatchObject({ from: "src/b.ts", to: "src/a.ts" });
    expect(edges[0]!.weight).toBeGreaterThan(0);
  });

  it("does not create self-reference edges", () => {
    const tags: Tag[] = [
      { file: "src/a.ts", name: "helper", kind: "def", line: 1, language: "typescript" },
      { file: "src/a.ts", name: "helper", kind: "ref", line: 10, language: "typescript" },
    ];
    const graph = DependencyGraph.build(tags);
    expect(graph.getEdges()).toHaveLength(0);
  });

  it("accumulates weight for multiple references between same files", () => {
    const tags: Tag[] = [
      { file: "src/a.ts", name: "foo", kind: "def", line: 1, language: "typescript" },
      { file: "src/a.ts", name: "bar", kind: "def", line: 5, language: "typescript" },
      { file: "src/b.ts", name: "foo", kind: "ref", line: 1, language: "typescript" },
      { file: "src/b.ts", name: "bar", kind: "ref", line: 2, language: "typescript" },
    ];
    const graph = DependencyGraph.build(tags);
    const edges = graph.getEdges();
    expect(edges).toHaveLength(1);
    expect(edges[0]!.weight).toBe(2);
  });

  it("tracks all files including isolated ones", () => {
    const tags: Tag[] = [
      { file: "src/a.ts", name: "foo", kind: "def", line: 1, language: "typescript" },
      { file: "src/isolated.ts", name: "standalone", kind: "def", line: 1, language: "typescript" },
    ];
    const graph = DependencyGraph.build(tags);
    expect(graph.getFiles().size).toBe(2);
    expect(graph.getFiles().has("src/isolated.ts")).toBe(true);
  });
});

// ---------- PageRank ----------

describe("PageRank", () => {
  it("boosts chatFiles above non-chatFiles", () => {
    const tags: Tag[] = [
      { file: "src/a.ts", name: "processItems", kind: "def", line: 1, language: "typescript" },
      { file: "src/b.ts", name: "processItems", kind: "ref", line: 1, language: "typescript" },
      { file: "src/b.ts", name: "helperMethod", kind: "def", line: 5, language: "typescript" },
      { file: "src/c.ts", name: "helperMethod", kind: "ref", line: 1, language: "typescript" },
    ];
    const graph = DependencyGraph.build(tags);

    const rankWithBoost = computePageRank(graph, tags, {
      chatFiles: new Set(["src/a.ts"]),
    });

    const rankWithout = computePageRank(graph, tags, {
      chatFiles: new Set(),
    });

    const aRankBoosted = rankWithBoost.get("src/a.ts") ?? 0;
    const aRankNormal = rankWithout.get("src/a.ts") ?? 0;
    expect(aRankBoosted).toBeGreaterThan(aRankNormal);
  });

  it("isolated files get base rank only", () => {
    const tags: Tag[] = [
      { file: "src/main.ts", name: "startServer", kind: "def", line: 1, language: "typescript" },
      { file: "src/helper.ts", name: "startServer", kind: "ref", line: 1, language: "typescript" },
      { file: "src/isolated.ts", name: "standalone", kind: "def", line: 1, language: "typescript" },
    ];
    const graph = DependencyGraph.build(tags);
    const rank = computePageRank(graph, tags);

    const mainRank = rank.get("src/main.ts") ?? 0;
    const isolatedRank = rank.get("src/isolated.ts") ?? 0;
    expect(mainRank).toBeGreaterThan(isolatedRank);
  });

  it("returns empty map for empty graph", () => {
    const graph = DependencyGraph.build([]);
    const rank = computePageRank(graph, []);
    expect(rank.size).toBe(0);
  });

  it("test files get lower rank than source files", () => {
    const tags: Tag[] = [
      { file: "src/engine.ts", name: "ContextEngine", kind: "def", line: 1, language: "typescript" },
      { file: "src/engine.test.ts", name: "ContextEngine", kind: "ref", line: 5, language: "typescript" },
      { file: "src/engine.test.ts", name: "testHelper", kind: "def", line: 1, language: "typescript" },
      { file: "src/main.ts", name: "ContextEngine", kind: "ref", line: 1, language: "typescript" },
    ];
    const graph = DependencyGraph.build(tags);
    const rank = computePageRank(graph, tags);

    const engineRank = rank.get("src/engine.ts") ?? 0;
    const testRank = rank.get("src/engine.test.ts") ?? 0;
    expect(engineRank).toBeGreaterThan(testRank);
  });
});

// ---------- RepoMapRenderer ----------

describe("RepoMapRenderer", () => {
  it("renders within token budget with fence format", () => {
    const tags: Tag[] = [
      { file: "src/a.ts", name: "processData", kind: "def", line: 1, language: "typescript" },
      { file: "src/a.ts", name: "helperFunc", kind: "def", line: 10, language: "typescript" },
      { file: "src/b.ts", name: "buildOutput", kind: "def", line: 1, language: "typescript" },
    ];
    const tagsByFile = new Map<string, Tag[]>();
    tagsByFile.set("src/a.ts", tags.filter((t) => t.file === "src/a.ts"));
    tagsByFile.set("src/b.ts", tags.filter((t) => t.file === "src/b.ts"));

    const ranked: [string, number][] = [
      ["src/a.ts", 0.6],
      ["src/b.ts", 0.4],
    ];

    const result = renderRepoMap(ranked, tagsByFile, { tokenBudget: 500 });
    expect(result).toMatch(/^<repo_map>/);
    expect(result).toMatch(/<\/repo_map>$/);
    expect(result).toContain("src/a.ts");
    expect(result).toContain("processData");
    expect(result).toContain("helperFunc");
  });

  it("returns empty string when budget is too small", () => {
    const tags: Tag[] = [
      { file: "src/a.ts", name: "processData", kind: "def", line: 1, language: "typescript" },
    ];
    const tagsByFile = new Map<string, Tag[]>();
    tagsByFile.set("src/a.ts", tags);

    const ranked: [string, number][] = [["src/a.ts", 1.0]];
    const result = renderRepoMap(ranked, tagsByFile, { tokenBudget: 1 });
    expect(result).toBe("");
  });

  it("respects token budget by binary search", () => {
    const tags: Tag[] = [];
    const tagsByFile = new Map<string, Tag[]>();
    const ranked: [string, number][] = [];

    for (let i = 0; i < 50; i++) {
      const file = `src/module${i}.ts`;
      const fileTags: Tag[] = [];
      for (let j = 0; j < 5; j++) {
        const tag: Tag = {
          file,
          name: `function${i}_${j}_longNameHere`,
          kind: "def",
          line: j * 10 + 1,
          language: "typescript",
        };
        fileTags.push(tag);
        tags.push(tag);
      }
      tagsByFile.set(file, fileTags);
      ranked.push([file, 1 / (i + 1)]);
    }

    const budget = 200;
    const result = renderRepoMap(ranked, tagsByFile, { tokenBudget: budget });
    if (result) {
      const estimatedTokens = Math.ceil(result.length / 4);
      expect(estimatedTokens).toBeLessThanOrEqual(budget * 1.2);
    }
  });
});

describe("getTokenBudget", () => {
  it("returns large budget for 900K context", () => {
    expect(getTokenBudget(900_000)).toBe(12_000);
  });

  it("returns medium budget for 200K context", () => {
    expect(getTokenBudget(200_000)).toBe(4_000);
  });

  it("returns subagent budget for small context", () => {
    expect(getTokenBudget(50_000)).toBe(2_000);
  });
});

// ---------- TagCache (SQLite) ----------

describe("TagCache", () => {
  let tmpDir: string;
  let dbPath: string;

  beforeAll(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "repomap-test-"));
    dbPath = path.join(tmpDir, "test-tags.sqlite");
  });

  afterAll(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns null for unknown file mtime", async () => {
    const cache = new TagCache(dbPath + ".fresh");
    await cache.init();
    expect(cache.getFileMtime("nonexistent.ts")).toBeNull();
    cache.close();
  });

  it("stores and retrieves tags", async () => {
    const cache = new TagCache(dbPath);
    await cache.init();

    const tags: Tag[] = [
      { file: "src/a.ts", name: "foo", kind: "def", line: 1, language: "typescript" },
      { file: "src/a.ts", name: "bar", kind: "ref", line: 5, language: "typescript" },
    ];
    cache.setTags("src/a.ts", tags, 1000);

    expect(cache.getFileMtime("src/a.ts")).toBe(1000);
    const retrieved = cache.getTags("src/a.ts");
    expect(retrieved).toHaveLength(2);
    expect(retrieved[0]!.name).toBe("foo");

    await cache.flush();
    cache.close();
  });

  it("returns cache hit for same mtime after reload", async () => {
    const cache = new TagCache(dbPath);
    await cache.init();

    expect(cache.getFileMtime("src/a.ts")).toBe(1000);
    const retrieved = cache.getTags("src/a.ts");
    expect(retrieved).toHaveLength(2);

    cache.close();
  });

  it("replaces tags on mtime change", async () => {
    const cache = new TagCache(dbPath);
    await cache.init();

    const newTags: Tag[] = [
      { file: "src/a.ts", name: "baz", kind: "def", line: 1, language: "typescript" },
    ];
    cache.setTags("src/a.ts", newTags, 2000);

    expect(cache.getFileMtime("src/a.ts")).toBe(2000);
    const retrieved = cache.getTags("src/a.ts");
    expect(retrieved).toHaveLength(1);
    expect(retrieved[0]!.name).toBe("baz");

    cache.close();
  });

  it("persists to disk and reloads", async () => {
    const persistPath = dbPath + ".persist";
    const cache1 = new TagCache(persistPath);
    await cache1.init();
    cache1.setTags("src/x.ts", [
      { file: "src/x.ts", name: "myFunc", kind: "def", line: 1, language: "typescript" },
    ], 999);
    await cache1.flush();
    cache1.close();

    const cache2 = new TagCache(persistPath);
    await cache2.init();
    expect(cache2.getFileMtime("src/x.ts")).toBe(999);
    const tags = cache2.getTags("src/x.ts");
    expect(tags).toHaveLength(1);
    expect(tags[0]!.name).toBe("myFunc");
    cache2.close();
  });

  it("getAllTags returns all stored tags", async () => {
    const allPath = dbPath + ".all";
    const cache = new TagCache(allPath);
    await cache.init();
    cache.setTags("src/a.ts", [
      { file: "src/a.ts", name: "foo", kind: "def", line: 1, language: "typescript" },
    ], 100);
    cache.setTags("src/b.ts", [
      { file: "src/b.ts", name: "bar", kind: "def", line: 1, language: "typescript" },
    ], 200);

    const all = cache.getAllTags();
    expect(all).toHaveLength(2);
    cache.close();
  });
});
