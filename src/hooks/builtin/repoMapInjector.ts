import fs from "node:fs/promises";
import path from "node:path";
import type { RegisteredHook, HookContext } from "../types.js";
import { extractTags } from "../../services/repomap/TagExtractor.js";
import { DependencyGraph } from "../../services/repomap/DependencyGraph.js";
import { computePageRank } from "../../services/repomap/PageRank.js";
import { renderRepoMap, getTokenBudget } from "../../services/repomap/RepoMapRenderer.js";
import { TagCache } from "../../services/repomap/TagCache.js";
import { getCachedRanks, setCachedRanks } from "../../services/repomap/PageRankCache.js";
import type { Tag, SupportedLanguage } from "../../services/repomap/types.js";
import { EXTENSION_TO_LANGUAGE, SKIP_DIRS } from "../../services/repomap/types.js";
import { isIncognitoMemoryMode } from "../../util/memoryMode.js";

const TAG_CACHE_FILENAME = ".core-agent/repo-tags.sqlite";
const SCAN_DEADLINE_MS = 10_000;

let tagCache: TagCache | null = null;
let tagCacheRoot: string | null = null;
let graphVersion = 0;

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_REPO_MAP;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function getContextWindow(): number {
  const raw = process.env.CORE_AGENT_CONTEXT_TOKENS;
  if (raw) {
    const n = parseInt(raw, 10);
    if (!isNaN(n) && n > 0) return n;
  }
  return 900_000;
}

async function ensureTagCache(workspaceRoot: string): Promise<TagCache> {
  if (tagCache && tagCacheRoot === workspaceRoot) return tagCache;

  if (tagCache) {
    await tagCache.flush().catch(() => {});
    tagCache.close();
  }

  const dbPath = path.join(workspaceRoot, TAG_CACHE_FILENAME);
  tagCache = new TagCache(dbPath);
  await tagCache.init();
  tagCacheRoot = workspaceRoot;
  return tagCache;
}

async function walkSourceFiles(
  root: string,
  dir: string,
  files: { relPath: string; absPath: string; lang: SupportedLanguage }[],
  deadline: number,
): Promise<void> {
  if (Date.now() > deadline) return;
  let dirents;
  try {
    dirents = await fs.readdir(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const d of dirents) {
    if (Date.now() > deadline) return;
    if (SKIP_DIRS.has(d.name)) continue;
    if (d.name.startsWith(".")) continue;

    const abs = path.join(dir, d.name);
    if (d.isDirectory()) {
      await walkSourceFiles(root, abs, files, deadline);
    } else if (d.isFile()) {
      const ext = path.extname(d.name);
      const lang = EXTENSION_TO_LANGUAGE[ext];
      if (lang) {
        const rel = path.relative(root, abs).split(path.sep).join("/");
        files.push({ relPath: rel, absPath: abs, lang });
      }
    }
  }
}

function extractChatFiles(transcript: ReadonlyArray<{ kind: string; [key: string]: unknown }>): Set<string> {
  const files = new Set<string>();
  for (const entry of transcript) {
    if (entry.kind === "tool_call") {
      const input = entry.input as Record<string, unknown> | undefined;
      if (!input) continue;
      for (const key of ["file_path", "path", "filePath", "file", "cwd"]) {
        const val = input[key];
        if (typeof val === "string" && val.length > 0) {
          files.add(val);
        }
      }
    }
    if (entry.kind === "user_message") {
      const text = entry.text as string | undefined;
      if (text) {
        const fileRefs = text.match(/(?:^|\s)([\w./\\-]+\.(?:ts|tsx|js|jsx|py|mjs|cjs))(?:\s|$|:|\()/g);
        if (fileRefs) {
          for (const ref of fileRefs) {
            files.add(ref.trim().replace(/[:(\s]/g, ""));
          }
        }
      }
    }
  }
  return files;
}

export interface RepoMapInjectorOptions {
  workspaceRoot: string;
}

export function makeRepoMapInjectorHook(
  opts: RepoMapInjectorOptions,
): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:repo-map-injector",
    point: "beforeLLMCall",
    priority: 8,
    blocking: false,
    handler: async (args, ctx: HookContext) => {
      try {
        if (isIncognitoMemoryMode(ctx.memoryMode)) return { action: "continue" };
        if (!isEnabled()) return { action: "continue" };
        if (args.iteration > 0) return { action: "continue" };
        if (args.system.includes("<repo_map")) return { action: "continue" };

        try {
          const st = await fs.stat(opts.workspaceRoot);
          if (!st.isDirectory()) return { action: "continue" };
        } catch {
          return { action: "continue" };
        }

        const cache = await ensureTagCache(opts.workspaceRoot);

        const chatFiles = extractChatFiles(ctx.transcript);

        const cached = getCachedRanks(chatFiles, graphVersion);
        if (cached) {
          const budget = getTokenBudget(getContextWindow());
          const tagsByFile = buildTagsByFile(cache.getAllTags());
          const ranked = sortByRank(cached);
          const fence = renderRepoMap(ranked, tagsByFile, { tokenBudget: budget });
          if (!fence) return { action: "continue" };
          return {
            action: "replace",
            value: { ...args, system: `${args.system}\n\n${fence}` },
          };
        }

        const deadline = Date.now() + SCAN_DEADLINE_MS;
        const sourceFiles: { relPath: string; absPath: string; lang: SupportedLanguage }[] = [];
        await walkSourceFiles(opts.workspaceRoot, opts.workspaceRoot, sourceFiles, deadline);

        let changed = false;
        for (const sf of sourceFiles) {
          if (Date.now() > deadline) break;
          try {
            const st = await fs.stat(sf.absPath);
            const cachedMtime = cache.getFileMtime(sf.relPath);
            if (cachedMtime !== null && cachedMtime === Math.floor(st.mtimeMs)) continue;

            const source = await fs.readFile(sf.absPath, "utf8");
            if (source.length > 500_000) continue;
            const tags = await extractTags(source, sf.relPath, sf.lang);
            cache.setTags(sf.relPath, tags, Math.floor(st.mtimeMs));
            changed = true;
          } catch {
            // skip unparseable files
          }
        }

        if (changed) {
          graphVersion++;
          cache.flush().catch(() => {});
        }

        const allTags = cache.getAllTags();
        if (allTags.length === 0) return { action: "continue" };

        const graph = DependencyGraph.build(allTags);
        const ranks = computePageRank(graph, allTags, { chatFiles });
        setCachedRanks(chatFiles, graphVersion, ranks);

        const budget = getTokenBudget(getContextWindow());
        const tagsByFile = buildTagsByFile(allTags);
        const ranked = sortByRank(ranks);
        const fence = renderRepoMap(ranked, tagsByFile, { tokenBudget: budget });

        if (!fence) return { action: "continue" };

        const nextSystem = args.system
          ? `${args.system}\n\n${fence}`
          : fence;
        return {
          action: "replace",
          value: { ...args, system: nextSystem },
        };
      } catch (err) {
        ctx.log("warn", "[repo-map-injector] inject failed; turn continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}

function buildTagsByFile(tags: Tag[]): Map<string, Tag[]> {
  const map = new Map<string, Tag[]>();
  for (const tag of tags) {
    let arr = map.get(tag.file);
    if (!arr) {
      arr = [];
      map.set(tag.file, arr);
    }
    arr.push(tag);
  }
  return map;
}

function sortByRank(ranks: Map<string, number>): [string, number][] {
  return [...ranks.entries()].sort((a, b) => b[1] - a[1]);
}

export function _resetRepoMapState(): void {
  if (tagCache) {
    tagCache.close();
    tagCache = null;
    tagCacheRoot = null;
  }
  graphVersion = 0;
}
