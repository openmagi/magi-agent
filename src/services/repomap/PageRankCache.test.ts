import { describe, it, expect, afterEach } from "vitest";
import { getCachedRanks, setCachedRanks, _clearPageRankCache } from "./PageRankCache.js";

describe("PageRankCache", () => {
  afterEach(() => {
    _clearPageRankCache();
  });

  it("returns null for cache miss", () => {
    const result = getCachedRanks(new Set(["a.ts"]), 1);
    expect(result).toBeNull();
  });

  it("returns cached ranks on hit", () => {
    const chatFiles = new Set(["a.ts", "b.ts"]);
    const ranks = new Map([["a.ts", 0.6], ["b.ts", 0.4]]);
    setCachedRanks(chatFiles, 1, ranks);

    const cached = getCachedRanks(chatFiles, 1);
    expect(cached).not.toBeNull();
    expect(cached!.get("a.ts")).toBe(0.6);
  });

  it("misses when graph version changes", () => {
    const chatFiles = new Set(["a.ts"]);
    setCachedRanks(chatFiles, 1, new Map([["a.ts", 0.5]]));

    const cached = getCachedRanks(chatFiles, 2);
    expect(cached).toBeNull();
  });

  it("misses when chatFiles change", () => {
    setCachedRanks(new Set(["a.ts"]), 1, new Map([["a.ts", 0.5]]));

    const cached = getCachedRanks(new Set(["b.ts"]), 1);
    expect(cached).toBeNull();
  });
});
