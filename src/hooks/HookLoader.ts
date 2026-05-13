/**
 * HookLoader — discovers, loads, and validates user-authored hooks
 * from the filesystem.
 *
 * Discovery order (highest priority wins on name collision):
 *   1. `./hooks/` (project-local)
 *   2. `~/.magi/hooks/` (user-global)
 *   3. `./node_modules/@magi-hooks/*‌/` (marketplace)
 *
 * Each `.ts` / `.js` file in a hook directory should default-export
 * (or named-export) a `RegisteredHook` or `RegisteredHook[]`.
 *
 * TypeScript files are transpiled at runtime via `tsx` (devDep). When
 * tsx is unavailable, `.ts` files are skipped with a warning and only
 * `.js` files are loaded.
 */

import fs from "node:fs";
import path from "node:path";
import os from "node:os";

import type { HookPoint, RegisteredHook } from "./types.js";

/* ------------------------------------------------------------------ */
/*  Validation                                                         */
/* ------------------------------------------------------------------ */

const VALID_HOOK_POINTS = new Set<string>([
  "beforeTurnStart",
  "afterTurnEnd",
  "beforeLLMCall",
  "afterLLMCall",
  "beforeToolUse",
  "afterToolUse",
  "beforeCommit",
  "afterCommit",
  "onAbort",
  "onError",
  "onTaskCheckpoint",
  "beforeCompaction",
  "afterCompaction",
  "onRuleViolation",
  "onArtifactCreated",
]);

function isValidHook(h: unknown): h is RegisteredHook {
  if (!h || typeof h !== "object") return false;
  const obj = h as Record<string, unknown>;
  return (
    typeof obj.name === "string" &&
    obj.name.length > 0 &&
    typeof obj.point === "string" &&
    VALID_HOOK_POINTS.has(obj.point) &&
    typeof obj.handler === "function"
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

function listHookFiles(dir: string): string[] {
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => {
      const ext = path.extname(f);
      return (ext === ".ts" || ext === ".js") && !f.endsWith(".test.ts") && !f.endsWith(".test.js") && !f.endsWith(".d.ts");
    })
    .map((f) => path.join(dir, f));
}

function listMarketplaceHookFiles(nodeModulesDir: string): string[] {
  const scopeDir = path.join(nodeModulesDir, "@magi-hooks");
  if (!fs.existsSync(scopeDir) || !fs.statSync(scopeDir).isDirectory()) {
    return [];
  }
  const files: string[] = [];
  for (const pkg of fs.readdirSync(scopeDir)) {
    const pkgDir = path.join(scopeDir, pkg);
    if (!fs.statSync(pkgDir).isDirectory()) continue;
    // Look for index.ts/js or any hook files in the package root
    files.push(...listHookFiles(pkgDir));
  }
  return files;
}

/* ------------------------------------------------------------------ */
/*  Module loading                                                     */
/* ------------------------------------------------------------------ */

/**
 * Try to dynamically import a TS file. Uses tsx register if available.
 * Falls back to native import for .js files.
 */
async function importModule(
  filePath: string,
): Promise<Record<string, unknown> | null> {
  const ext = path.extname(filePath);
  if (ext === ".ts") {
    try {
      // tsx register is available as a devDep in this project
      // Use pathToFileURL for cross-platform compatibility
      const { pathToFileURL } = await import("node:url");
      const fileUrl = pathToFileURL(filePath).href;
      return (await import(/* @vite-ignore */ fileUrl)) as Record<string, unknown>;
    } catch {
      // tsx not available or import failed — skip .ts files
      return null;
    }
  }
  // .js file — direct import
  try {
    const { pathToFileURL } = await import("node:url");
    const fileUrl = pathToFileURL(filePath).href;
    return (await import(/* @vite-ignore */ fileUrl)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function extractHooks(
  mod: Record<string, unknown>,
  source: string,
  warnings: string[],
): RegisteredHook[] {
  const hooks: RegisteredHook[] = [];

  // Check default export first
  const defaultExport = mod.default;
  if (defaultExport) {
    if (Array.isArray(defaultExport)) {
      for (const item of defaultExport) {
        if (isValidHook(item)) {
          hooks.push(item);
        } else {
          warnings.push(
            `Invalid hook in default export array from ${source}`,
          );
        }
      }
    } else if (isValidHook(defaultExport)) {
      hooks.push(defaultExport);
    }
  }

  // Check named exports
  for (const [key, value] of Object.entries(mod)) {
    if (key === "default" || key === "__esModule") continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (isValidHook(item)) hooks.push(item);
      }
    } else if (isValidHook(value)) {
      hooks.push(value);
    }
  }

  return hooks;
}

/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */

export interface HookLoaderResult {
  hooks: RegisteredHook[];
  warnings: string[];
}

export interface HookLoaderOptions {
  /** Project-local hook directory (default: `./hooks`). */
  directory?: string;
  /** User-global hook directory (default: `~/.magi/hooks`). */
  globalDirectory?: string;
  /** Working directory root (default: `process.cwd()`). */
  workspaceRoot?: string;
}

/**
 * Discover, load, and validate user hooks from the filesystem.
 *
 * On name collision, the higher-priority source wins and a warning is
 * emitted. Priority order: project-local > user-global > marketplace.
 */
export async function loadUserHooks(
  opts: HookLoaderOptions = {},
): Promise<HookLoaderResult> {
  const cwd = opts.workspaceRoot ?? process.cwd();
  const localDir = path.resolve(cwd, opts.directory ?? "./hooks");
  const globalDir = resolveHomePath(opts.globalDirectory ?? "~/.magi/hooks");
  const marketplaceDir = path.resolve(cwd, "node_modules");

  const warnings: string[] = [];
  const seenNames = new Map<string, string>(); // name -> source
  const result: RegisteredHook[] = [];

  // Sources in priority order (highest first)
  const sources: Array<{ label: string; files: string[] }> = [
    { label: "project-local", files: listHookFiles(localDir) },
    { label: "user-global", files: listHookFiles(globalDir) },
    {
      label: "marketplace",
      files: listMarketplaceHookFiles(marketplaceDir),
    },
  ];

  for (const { label, files } of sources) {
    for (const filePath of files) {
      const mod = await importModule(filePath);
      if (!mod) {
        warnings.push(`Failed to load hook file: ${filePath}`);
        continue;
      }

      const hooks = extractHooks(mod, filePath, warnings);

      for (const hook of hooks) {
        const existing = seenNames.get(hook.name);
        if (existing) {
          warnings.push(
            `Hook name collision: "${hook.name}" from ${label} (${filePath}) ` +
              `already registered from ${existing}. Skipping.`,
          );
          continue;
        }
        seenNames.set(hook.name, `${label} (${filePath})`);
        result.push(hook);
      }
    }
  }

  return { hooks: result, warnings };
}
