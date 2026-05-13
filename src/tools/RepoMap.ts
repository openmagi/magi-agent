/**
 * RepoMap — lightweight repository structure + symbol index.
 *
 * Returns a compact tree of the workspace file layout plus extracted
 * symbol definitions (functions, classes, interfaces, types, constants)
 * and import statements from source files. Designed to be called once
 * at the start of coding work so the LLM has immediate structural
 * awareness of the codebase without needing multiple Grep/Glob calls.
 *
 * No AST parser — uses fast regex-based extraction (same approach as
 * CodeSymbolSearch) on a broader set of files. Skips node_modules,
 * .git, dist, build, and other non-source directories.
 *
 * Note: This is the on-demand tool version. The PageRank-based
 * RepositoryMap hook (auto-inject) coexists separately.
 */

import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";

export interface RepoMapInput {
  path?: string;
  maxFiles?: number;
  maxDepth?: number;
}

export interface FileSymbols {
  file: string;
  definitions: string[];
  imports?: string[];
}

export interface RepoMapOutput {
  tree: string[];
  symbols: FileSymbols[];
  fileCount: number;
  truncated: boolean;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    path: {
      type: "string",
      description: "Workspace-relative subdirectory to map. Default: workspace root.",
    },
    maxFiles: {
      type: "integer",
      minimum: 1,
      maximum: 500,
      description: "Maximum source files to extract symbols from. Default: 200.",
    },
    maxDepth: {
      type: "integer",
      minimum: 1,
      maximum: 10,
      description: "Maximum directory depth. Default: 6.",
    },
  },
} as const;

const SKIP_DIRS = new Set([
  "node_modules",
  ".git",
  ".next",
  "dist",
  "build",
  "coverage",
  ".turbo",
  ".venv",
  "__pycache__",
  ".cache",
  ".DS_Store",
  ".tsbuildinfo",
  "vendor",
  ".mypy_cache",
  ".ruff_cache",
  ".pytest_cache",
  "target",
]);

const SOURCE_EXTENSIONS = new Set([
  ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
  ".py",
  ".go",
  ".rs",
  ".java", ".kt",
  ".swift",
  ".rb",
  ".c", ".cpp", ".h", ".hpp",
]);

const CONFIG_EXTENSIONS = new Set([
  ".json", ".yaml", ".yml", ".toml", ".md", ".txt",
  ".env", ".sh", ".sql", ".graphql",
]);

const DEFINITION_PATTERNS: RegExp = new RegExp(
  [
    String.raw`\b(?:export\s+)?(?:async\s+)?function\s+(\w+)`,
    String.raw`\b(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=`,
    String.raw`\b(?:export\s+)?(?:class|interface|type|enum)\s+(\w+)`,
    String.raw`^\s*def\s+(\w+)`,
    String.raw`^\s*class\s+(\w+)`,
    String.raw`^\s*func\s+(\w+)`,
    String.raw`^\s*(?:pub\s+)?(?:fn|struct|enum|trait|impl)\s+(\w+)`,
  ].join("|"),
);

const IMPORT_PATTERN = /^(?:import\s+.+?from\s+['"](.+?)['"]|import\s+['"](.+?)['"]|from\s+(\S+)\s+import)/;

interface WalkEntry {
  relPath: string;
  absPath: string;
  isDir: boolean;
  depth: number;
}

async function walkTree(
  root: string,
  base: string,
  maxDepth: number,
  maxFiles: number,
): Promise<{ entries: WalkEntry[]; sourceFiles: WalkEntry[]; totalFiles: number }> {
  const entries: WalkEntry[] = [];
  const sourceFiles: WalkEntry[] = [];
  let totalFiles = 0;

  async function recurse(dir: string, relPrefix: string, depth: number): Promise<void> {
    if (depth > maxDepth) return;
    let dirents;
    try {
      dirents = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    dirents.sort((a, b) => {
      if (a.isDirectory() !== b.isDirectory()) return a.isDirectory() ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const d of dirents) {
      if (d.name.startsWith(".") && SKIP_DIRS.has(d.name)) continue;
      if (SKIP_DIRS.has(d.name)) continue;

      const abs = path.join(dir, d.name);
      const rel = relPrefix ? `${relPrefix}/${d.name}` : d.name;

      if (d.isDirectory()) {
        entries.push({ relPath: rel, absPath: abs, isDir: true, depth });
        await recurse(abs, rel, depth + 1);
      } else if (d.isFile()) {
        totalFiles++;
        const ext = path.extname(d.name).toLowerCase();
        if (SOURCE_EXTENSIONS.has(ext) || CONFIG_EXTENSIONS.has(ext)) {
          entries.push({ relPath: rel, absPath: abs, isDir: false, depth });
        }
        if (SOURCE_EXTENSIONS.has(ext) && sourceFiles.length < maxFiles) {
          sourceFiles.push({ relPath: rel, absPath: abs, isDir: false, depth });
        }
      }
    }
  }

  await recurse(base, path.relative(root, base) || "", 0);
  return { entries, sourceFiles, totalFiles };
}

function extractDefinitions(content: string): string[] {
  const defs: string[] = [];
  for (const line of content.split("\n")) {
    const trimmed = line.trimStart();
    if (trimmed.startsWith("//") || trimmed.startsWith("#") || trimmed.startsWith("*")) continue;
    const match = DEFINITION_PATTERNS.exec(trimmed);
    if (match) {
      const name = match.slice(1).find((g) => g !== undefined);
      if (name && name.length > 1) {
        const prefix = trimmed.slice(0, 60).trim();
        defs.push(prefix.length < trimmed.length ? `${prefix}...` : prefix);
      }
    }
  }
  return defs;
}

function extractImports(content: string): string[] {
  const imports: string[] = [];
  for (const line of content.split("\n")) {
    const match = IMPORT_PATTERN.exec(line.trim());
    if (match) {
      const mod = match[1] ?? match[2] ?? match[3];
      if (mod) imports.push(mod);
    }
  }
  return imports;
}

function formatTree(entries: WalkEntry[]): string[] {
  return entries.map((e) => {
    const indent = "  ".repeat(e.depth);
    return `${indent}${e.isDir ? `${path.basename(e.relPath)}/` : path.basename(e.relPath)}`;
  });
}

export function makeRepoMapTool(workspaceRoot: string): Tool<RepoMapInput, RepoMapOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "RepoMap",
    description:
      "Build a compact map of the repository structure with symbol definitions and imports. " +
      "Use at the start of coding work to understand the codebase layout before making changes. " +
      "Returns directory tree, file-level symbol definitions (functions, classes, types), and import graph.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    shouldDefer: true,
    kind: "core",
    mutatesWorkspace: false,
    isConcurrencySafe: true,
    tags: ["coding", "exploratory"],
    validate(input) {
      if (input?.maxFiles !== undefined) {
        if (!Number.isInteger(input.maxFiles) || input.maxFiles < 1 || input.maxFiles > 500) {
          return "`maxFiles` must be an integer in [1..500]";
        }
      }
      if (input?.maxDepth !== undefined) {
        if (!Number.isInteger(input.maxDepth) || input.maxDepth < 1 || input.maxDepth > 10) {
          return "`maxDepth` must be an integer in [1..10]";
        }
      }
      return null;
    },
    async execute(input: RepoMapInput, ctx: ToolContext): Promise<ToolResult<RepoMapOutput>> {
      const start = Date.now();
      try {
        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        const base = input.path ? ws.resolve(input.path) : ws.root;
        const maxFiles = Math.min(500, Math.max(1, input.maxFiles ?? 200));
        const maxDepth = Math.min(10, Math.max(1, input.maxDepth ?? 6));

        const { entries, sourceFiles, totalFiles } = await walkTree(ws.root, base, maxDepth, maxFiles);
        const truncated = sourceFiles.length >= maxFiles;
        const tree = formatTree(entries);

        const symbols: FileSymbols[] = [];
        for (const sf of sourceFiles) {
          const content = await fs.readFile(sf.absPath, "utf8").catch(() => "");
          if (!content) continue;
          const defs = extractDefinitions(content);
          const imps = extractImports(content);
          if (defs.length > 0 || imps.length > 0) {
            symbols.push({
              file: sf.relPath,
              definitions: defs,
              ...(imps.length > 0 ? { imports: imps } : {}),
            });
          }
        }

        return {
          status: "ok",
          output: { tree, symbols, fileCount: totalFiles, truncated },
          metadata: {
            evidenceKind: "repo_map",
            fileCount: totalFiles,
            symbolFileCount: symbols.length,
            truncated,
          },
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
