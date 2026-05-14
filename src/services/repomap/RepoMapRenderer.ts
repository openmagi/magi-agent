import type { Tag } from "./types.js";

export interface RenderOptions {
  tokenBudget: number;
  tolerance?: number;
}

export type ModelTier = "large" | "medium" | "subagent";

export const TOKEN_BUDGETS: Record<ModelTier, number> = {
  large: 12_000,
  medium: 4_000,
  subagent: 2_000,
};

function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

function renderFileBlock(file: string, defs: Tag[]): string {
  const lines: string[] = [file];
  const sorted = [...defs].sort((a, b) => a.line - b.line);
  for (const def of sorted) {
    lines.push(`  ${def.name} (L${def.line})`);
  }
  return lines.join("\n");
}

export function renderRepoMap(
  rankedFiles: [string, number][],
  tagsByFile: Map<string, Tag[]>,
  opts: RenderOptions,
): string {
  const tolerance = opts.tolerance ?? 0.15;
  const maxTokens = opts.tokenBudget * (1 + tolerance);

  let lo = 0;
  let hi = rankedFiles.length;
  let bestCount = 0;

  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2);
    const rendered = buildMapContent(rankedFiles.slice(0, mid), tagsByFile);
    const tokens = estimateTokens(rendered);

    if (tokens <= maxTokens) {
      bestCount = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }

  if (bestCount === 0) return "";

  const content = buildMapContent(rankedFiles.slice(0, bestCount), tagsByFile);
  return `<repo_map>\n${content}\n</repo_map>`;
}

function buildMapContent(
  files: [string, number][],
  tagsByFile: Map<string, Tag[]>,
): string {
  const blocks: string[] = [];
  for (const [file] of files) {
    const tags = tagsByFile.get(file);
    if (!tags) continue;
    const defs = tags.filter((t) => t.kind === "def");
    if (defs.length === 0) continue;
    blocks.push(renderFileBlock(file, defs));
  }
  return blocks.join("\n\n");
}

export function getTokenBudget(contextWindow: number): number {
  if (contextWindow >= 500_000) return TOKEN_BUDGETS.large;
  if (contextWindow >= 100_000) return TOKEN_BUDGETS.medium;
  return TOKEN_BUDGETS.subagent;
}
