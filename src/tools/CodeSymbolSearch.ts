import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";

export interface CodeSymbolSearchInput {
  symbol: string;
  cwd?: string;
  extensions?: string[];
  maxResults?: number;
}

export interface CodeSymbolSearchResult {
  file: string;
  line: number;
  preview: string;
}

export interface CodeSymbolSearchOutput {
  symbol: string;
  results: CodeSymbolSearchResult[];
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    symbol: { type: "string", description: "Exact symbol name to find." },
    cwd: { type: "string", description: "Workspace-relative directory to search." },
    extensions: {
      type: "array",
      items: { type: "string" },
      description: "File extensions to include, such as .ts or .py.",
    },
    maxResults: { type: "integer", minimum: 1, description: "Maximum results to return." },
  },
  required: ["symbol"],
} as const;

const DEFAULT_EXTENSIONS = new Set([
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
  ".py",
  ".go",
  ".rs",
  ".java",
  ".kt",
  ".swift",
]);

const SKIP_DIRS = new Set([
  ".git",
  "node_modules",
  ".next",
  "dist",
  "build",
  "coverage",
  ".turbo",
  ".venv",
  "__pycache__",
]);

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function definitionPattern(symbol: string): RegExp {
  const s = escapeRegExp(symbol);
  return new RegExp(
    [
      `\\b(?:export\\s+)?(?:async\\s+)?function\\s+${s}\\b`,
      `\\b(?:export\\s+)?(?:const|let|var)\\s+${s}\\b`,
      `\\b(?:export\\s+)?(?:class|interface|type|enum)\\s+${s}\\b`,
      `^\\s*def\\s+${s}\\b`,
      `^\\s*func\\s+${s}\\b`,
    ].join("|"),
  );
}

async function walk(root: string, dir: string, extensions: Set<string>, files: string[]): Promise<void> {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name)) {
        await walk(root, path.join(dir, entry.name), extensions, files);
      }
      continue;
    }
    if (!entry.isFile()) continue;
    const ext = path.extname(entry.name);
    if (extensions.has(ext)) files.push(path.join(dir, entry.name));
  }
}

function workspaceRelative(root: string, file: string): string {
  return path.relative(root, file).split(path.sep).join("/");
}

export function makeCodeSymbolSearchTool(
  workspaceRoot: string,
): Tool<CodeSymbolSearchInput, CodeSymbolSearchOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "CodeSymbolSearch",
    description:
      "Find likely code symbol definitions with file and line evidence. For TypeScript/JavaScript, prefer CodeIntelligence first for semantic definition/reference/hover results. Use this as a fallback when CodeIntelligence cannot load the project or for non-TypeScript languages, before broad grep.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    mutatesWorkspace: false,
    isConcurrencySafe: true,
    validate(input) {
      if (!input || typeof input.symbol !== "string" || input.symbol.trim().length === 0) {
        return "`symbol` is required";
      }
      return null;
    },
    async execute(
      input: CodeSymbolSearchInput,
      ctx: ToolContext,
    ): Promise<ToolResult<CodeSymbolSearchOutput>> {
      const start = Date.now();
      try {
        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        const root = input.cwd ? ws.resolve(input.cwd) : ws.root;
        const extensions = new Set(
          (input.extensions ?? [...DEFAULT_EXTENSIONS]).map((ext) =>
            ext.startsWith(".") ? ext : `.${ext}`,
          ),
        );
        const maxResults = Math.min(200, Math.max(1, input.maxResults ?? 50));
        const files: string[] = [];
        await walk(ws.root, root, extensions, files);
        const pattern = definitionPattern(input.symbol);
        const results: CodeSymbolSearchResult[] = [];
        for (const file of files.sort()) {
          const content = await fs.readFile(file, "utf8").catch(() => "");
          const lines = content.split("\n");
          for (let i = 0; i < lines.length; i++) {
            const line = lines[i] ?? "";
            if (!pattern.test(line)) continue;
            results.push({
              file: workspaceRelative(ws.root, file),
              line: i + 1,
              preview: line.trim(),
            });
            if (results.length >= maxResults) {
              return {
                status: "ok",
                output: { symbol: input.symbol, results },
                durationMs: Date.now() - start,
              };
            }
          }
        }
        return {
          status: "ok",
          output: { symbol: input.symbol, results },
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
