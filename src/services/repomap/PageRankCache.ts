import { createHash } from "node:crypto";

export interface PageRankCacheEntry {
  ranks: Map<string, number>;
  expiresAt: number;
}

const CACHE_TTL_MS = 2 * 60 * 1000;

const cache = new Map<string, PageRankCacheEntry>();

export function getCachedRanks(chatFiles: Set<string>, graphVersion: number): Map<string, number> | null {
  const key = makeCacheKey(chatFiles, graphVersion);
  const entry = cache.get(key);
  if (!entry) return null;
  if (entry.expiresAt < Date.now()) {
    cache.delete(key);
    return null;
  }
  return entry.ranks;
}

export function setCachedRanks(chatFiles: Set<string>, graphVersion: number, ranks: Map<string, number>): void {
  const key = makeCacheKey(chatFiles, graphVersion);
  cache.set(key, { ranks, expiresAt: Date.now() + CACHE_TTL_MS });

  if (cache.size > 50) {
    const now = Date.now();
    for (const [k, v] of cache) {
      if (v.expiresAt < now) cache.delete(k);
    }
  }
}

function makeCacheKey(chatFiles: Set<string>, graphVersion: number): string {
  const sorted = [...chatFiles].sort().join("\0");
  return createHash("sha256")
    .update(`${sorted}\n${graphVersion}`)
    .digest("hex")
    .slice(0, 16);
}

export function _clearPageRankCache(): void {
  cache.clear();
}
