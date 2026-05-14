/**
 * ToolLoader — discovers, loads, and validates user-authored tools
 * from the filesystem.
 *
 * Discovery order (highest priority wins on name collision):
 *   1. `./tools/` (project-local)
 *   2. `~/.magi/tools/` (user-global)
 *   3. `./node_modules/@magi-tools/<name>/` (marketplace)
 *
 * Each tool directory should contain an `index.ts` or `index.js` that
 * exports a factory function returning a `Tool` object.
 *
 * Follows the same patterns as HookLoader.
 */

import fs from "node:fs";
import path from "node:path";
import os from "node:os";

import type { Tool } from "../Tool.js";

/* ------------------------------------------------------------------ */
/*  Validation                                                         */
/* ------------------------------------------------------------------ */

const VALID_PERMISSIONS = new Set([
  "read",
  "write",
  "execute",
  "net",
  "meta",
]);

function isValidTool(t: unknown): t is Tool {
  if (!t || typeof t !== "object") return false;
  const obj = t as Record<string, unknown>;
  return (
    typeof obj.name === "string" &&
    obj.name.length > 0 &&
    typeof obj.description === "string" &&
    obj.description.length > 0 &&
    typeof obj.inputSchema === "object" &&
    obj.inputSchema !== null &&
    typeof obj.permission === "string" &&
    VALID_PERMISSIONS.has(obj.permission) &&
    typeof obj.execute === "function"
  );
}

/* ------------------------------------------------------------------ */
/*  File discovery                                                     */
/* ------------------------------------------------------------------ */

function resolveHomePath(p: string): string {
  if (p.startsWith("~/") || p === "~") {
    return path.join(os.homedir(), p.slice(2));
  }
  return p;
}

/**
 * List tool subdirectories in a parent directory. Each subdirectory
 * must contain an `index.ts` or `index.js` entry point.
 */
function listToolDirs(dir: string): Array<{ name: string; entryPath: string }> {
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) return [];
  const result: Array<{ name: string; entryPath: string }> = [];
  for (const entry of fs.readdirSync(dir)) {
    const subdir = path.join(dir, entry);
    if (!fs.statSync(subdir).isDirectory()) continue;
    // Skip common non-tool dirs
    if (entry.startsWith(".") || entry === "__fixtures__" || entry === "node_modules") continue;
    const tsEntry = path.join(subdir, "index.ts");
    const jsEntry = path.join(subdir, "index.js");
    if (fs.existsSync(tsEntry)) {
      result.push({ name: entry, entryPath: tsEntry });
    } else if (fs.existsSync(jsEntry)) {
      result.push({ name: entry, entryPath: jsEntry });
    }
  }
  return result;
}

function listMarketplaceToolDirs(
  nodeModulesDir: string,
): Array<{ name: string; entryPath: string }> {
  const scopeDir = path.join(nodeModulesDir, "@magi-tools");
  if (!fs.existsSync(scopeDir) || !fs.statSync(scopeDir).isDirectory()) {
    return [];
  }
  const result: Array<{ name: string; entryPath: string }> = [];
  for (const pkg of fs.readdirSync(scopeDir)) {
    const pkgDir = path.join(scopeDir, pkg);
    if (!fs.statSync(pkgDir).isDirectory()) continue;
    const tsEntry = path.join(pkgDir, "index.ts");
    const jsEntry = path.join(pkgDir, "index.js");
    if (fs.existsSync(tsEntry)) {
      result.push({ name: pkg, entryPath: tsEntry });
    } else if (fs.existsSync(jsEntry)) {
      result.push({ name: pkg, entryPath: jsEntry });
    }
  }
  return result;
}

/* ------------------------------------------------------------------ */
/*  Module loading                                                     */
/* ------------------------------------------------------------------ */

async function importModule(
  filePath: string,
): Promise<Record<string, unknown> | null> {
  const ext = path.extname(filePath);
  if (ext === ".ts") {
    try {
      const { pathToFileURL } = await import("node:url");
      const fileUrl = pathToFileURL(filePath).href;
      return (await import(/* @vite-ignore */ fileUrl)) as Record<
        string,
        unknown
      >;
    } catch {
      return null;
    }
  }
  try {
    const { pathToFileURL } = await import("node:url");
    const fileUrl = pathToFileURL(filePath).href;
    return (await import(/* @vite-ignore */ fileUrl)) as Record<
      string,
      unknown
    >;
  } catch {
    return null;
  }
}

/**
 * Extract a Tool from a module. Looks for:
 * 1. A named export that is a function matching `make*Tool` pattern
 * 2. A default export that is a function (factory)
 * 3. A default export that is already a Tool object
 */
function extractTool(
  mod: Record<string, unknown>,
  source: string,
  warnings: string[],
): Tool | null {
  // 1. Named export factory function (make*Tool pattern)
  for (const [key, value] of Object.entries(mod)) {
    if (key === "default" || key === "__esModule") continue;
    if (typeof value === "function" && /^make\w+Tool$/.test(key)) {
      try {
        const result = (value as () => unknown)();
        if (isValidTool(result)) return result;
        warnings.push(
          `Factory ${key} in ${source} returned invalid tool object`,
        );
      } catch (err) {
        warnings.push(
          `Factory ${key} in ${source} threw: ${(err as Error).message}`,
        );
      }
    }
  }

  // 2. Default export as factory function
  const defaultExport = mod.default;
  if (typeof defaultExport === "function") {
    try {
      const result = (defaultExport as () => unknown)();
      if (isValidTool(result)) return result;
    } catch {
      // Fall through to object check
    }
  }

  // 3. Default export as Tool object
  if (isValidTool(defaultExport)) {
    return defaultExport;
  }

  // 4. Any named export that is a valid Tool
  for (const [key, value] of Object.entries(mod)) {
    if (key === "default" || key === "__esModule") continue;
    if (isValidTool(value)) return value;
  }

  warnings.push(`No valid tool found in ${source}`);
  return null;
}

/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */

export interface ToolLoaderResult {
  tools: Tool[];
  warnings: string[];
}

export interface ToolLoaderOptions {
  /** Project-local tool directory (default: `./tools`). */
  directory?: string;
  /** User-global tool directory (default: `~/.magi/tools`). */
  globalDirectory?: string;
  /** Working directory root (default: `process.cwd()`). */
  workspaceRoot?: string;
}

/**
 * Discover, load, and validate user tools from the filesystem.
 *
 * On name collision, the higher-priority source wins and a warning is
 * emitted. Priority order: project-local > user-global > marketplace.
 */
export async function loadUserTools(
  opts: ToolLoaderOptions = {},
): Promise<ToolLoaderResult> {
  const cwd = opts.workspaceRoot ?? process.cwd();
  const localDir = path.resolve(cwd, opts.directory ?? "./tools");
  const globalDir = resolveHomePath(
    opts.globalDirectory ?? "~/.magi/tools",
  );
  const marketplaceDir = path.resolve(cwd, "node_modules");

  const warnings: string[] = [];
  const seenNames = new Map<string, string>(); // name -> source label
  const result: Tool[] = [];

  const sources: Array<{
    label: string;
    dirs: Array<{ name: string; entryPath: string }>;
  }> = [
    { label: "project-local", dirs: listToolDirs(localDir) },
    { label: "user-global", dirs: listToolDirs(globalDir) },
    {
      label: "marketplace",
      dirs: listMarketplaceToolDirs(marketplaceDir),
    },
  ];

  for (const { label, dirs } of sources) {
    for (const { name: dirName, entryPath } of dirs) {
      const mod = await importModule(entryPath);
      if (!mod) {
        warnings.push(`Failed to load tool from: ${entryPath}`);
        continue;
      }

      const tool = extractTool(mod, entryPath, warnings);
      if (!tool) continue;

      const existing = seenNames.get(tool.name);
      if (existing) {
        warnings.push(
          `Tool name collision: "${tool.name}" from ${label} (${dirName}) ` +
            `already registered from ${existing}. Skipping.`,
        );
        continue;
      }

      seenNames.set(tool.name, `${label} (${dirName})`);
      result.push(tool);
    }
  }

  return { tools: result, warnings };
}
