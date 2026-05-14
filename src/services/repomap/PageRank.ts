import type { Tag } from "./types.js";
import { TEST_FILE_PATTERNS, GENERATED_VENDOR_PATTERNS } from "./types.js";
import { DependencyGraph } from "./DependencyGraph.js";

export interface PageRankOptions {
  chatFiles?: Set<string>;
  dampingFactor?: number;
  iterations?: number;
}

const CAMEL_CASE_RE = /^[a-z][a-zA-Z0-9]{7,}$/;
const SNAKE_CASE_RE = /^[a-z][a-z0-9_]{7,}$/;

function isWellNamed(name: string): boolean {
  return CAMEL_CASE_RE.test(name) || SNAKE_CASE_RE.test(name);
}

function isTestFile(file: string): boolean {
  return TEST_FILE_PATTERNS.some((p) => p.test(file));
}

function isGeneratedOrVendor(file: string): boolean {
  return GENERATED_VENDOR_PATTERNS.some((p) => p.test(file));
}

function isPrivateModule(file: string): boolean {
  const parts = file.split("/");
  const name = parts[parts.length - 1] ?? "";
  return name.startsWith("_") && !name.startsWith("__");
}

function computeWellNamedRatio(file: string, defTags: Tag[]): number {
  const fileDefs = defTags.filter((t) => t.file === file);
  if (fileDefs.length === 0) return 0;
  const wellNamed = fileDefs.filter((t) => isWellNamed(t.name)).length;
  return wellNamed / fileDefs.length;
}

export function computePageRank(
  graph: DependencyGraph,
  allTags: Tag[],
  opts: PageRankOptions = {},
): Map<string, number> {
  const chatFiles = opts.chatFiles ?? new Set<string>();
  const damping = opts.dampingFactor ?? 0.85;
  const iterations = opts.iterations ?? 20;

  const allFiles = graph.getFiles();
  if (allFiles.size === 0) return new Map();

  const defTags = allTags.filter((t) => t.kind === "def");

  const personalization = new Map<string, number>();
  let totalP = 0;

  for (const file of allFiles) {
    let boost = 1.0;

    if (chatFiles.has(file)) boost = 50.0;

    if (computeWellNamedRatio(file, defTags) > 0.5) boost *= 10.0;

    if (isPrivateModule(file)) boost *= 0.1;
    if (isTestFile(file)) boost *= 0.5;
    if (isGeneratedOrVendor(file)) boost *= 0.01;

    personalization.set(file, boost);
    totalP += boost;
  }

  if (totalP > 0) {
    for (const [file, val] of personalization) {
      personalization.set(file, val / totalP);
    }
  }

  const N = allFiles.size;
  const rank = new Map<string, number>();
  for (const file of allFiles) {
    rank.set(file, 1 / N);
  }

  for (let iter = 0; iter < iterations; iter++) {
    const newRank = new Map<string, number>();

    for (const file of allFiles) {
      let r = (1 - damping) * (personalization.get(file) ?? 1 / N);

      const incoming = graph.getIncomingEdges(file);
      for (const edge of incoming) {
        const sourceRank = rank.get(edge.from) ?? 0;
        const outDegree = graph.getOutDegree(edge.from);
        if (outDegree > 0) {
          r += damping * (sourceRank * edge.weight) / outDegree;
        }
      }

      newRank.set(file, r);
    }

    let sum = 0;
    for (const v of newRank.values()) sum += v;
    if (sum > 0) {
      for (const [k, v] of newRank) newRank.set(k, v / sum);
    }

    for (const [k, v] of newRank) rank.set(k, v);
  }

  return rank;
}
