/**
 * ExternalHookLoader — discover + load user-authored hooks from workspace.
 * Design reference: §7.12 Phase 3 (extensible hooks).
 *
 * External hooks live in the workspace under a configurable directory
 * (default `./hooks`). Each file exports a `createHook(sdk, config)` factory
 * that returns a `RegisteredHook`-shaped object with a `custom:` prefixed
 * name. This keeps user-authored hooks sandboxed: they cannot claim built-in
 * names, and the registry can distinguish custom from platform hooks.
 */

import { readdir } from "node:fs/promises";
import { join } from "node:path";
import type { HookRegistry } from "./HookRegistry.js";
import type { HookPoint, RegisteredHook } from "./types.js";

const HOOK_FILE_EXTENSIONS = [".hook.js", ".hook.mjs"];
const CUSTOM_PREFIX = "custom:";
const VALID_HOOK_POINTS: ReadonlySet<string> = new Set<HookPoint>([
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

export interface ExternalHookSdk {
  readonly version: string;
}

export interface ExternalHookConfig {
  directory: string;
  autoDiscover?: boolean;
  hooks?: Array<{
    file: string;
    enabled?: boolean;
    priority?: number;
    config?: Record<string, unknown>;
  }>;
}

interface LoadResult {
  loaded: string[];
  failed: Array<{ file: string; error: string }>;
}

function createSdk(): ExternalHookSdk {
  return Object.freeze({ version: "1.0.0" });
}

function isValidHookPoint(point: string): point is HookPoint {
  return VALID_HOOK_POINTS.has(point);
}

function isHookFile(filename: string): boolean {
  return HOOK_FILE_EXTENSIONS.some((ext) => filename.endsWith(ext));
}

function validateHookDefinition(
  hook: unknown,
  filename: string,
): string | null {
  if (!hook || typeof hook !== "object") {
    return `${filename}: createHook did not return an object`;
  }
  const h = hook as Record<string, unknown>;
  if (typeof h.name !== "string" || !h.name) {
    return `${filename}: missing hook name`;
  }
  if (!h.name.startsWith(CUSTOM_PREFIX)) {
    return `${filename}: hook name must start with "${CUSTOM_PREFIX}"`;
  }
  if (typeof h.point !== "string" || !isValidHookPoint(h.point)) {
    return `${filename}: invalid hook point "${String(h.point)}"`;
  }
  if (typeof h.handler !== "function") {
    return `${filename}: handler must be a function`;
  }
  return null;
}

export async function loadExternalHooks(
  registry: HookRegistry,
  config: ExternalHookConfig,
  log: (level: "info" | "warn" | "error", msg: string, data?: Record<string, unknown>) => void,
): Promise<LoadResult> {
  const result: LoadResult = { loaded: [], failed: [] };
  const sdk = createSdk();

  const filesToLoad: Array<{
    path: string;
    priority?: number;
    enabled?: boolean;
    config?: Record<string, unknown>;
  }> = [];

  // Explicit hooks from config
  if (config.hooks) {
    for (const hookCfg of config.hooks) {
      if (hookCfg.enabled === false) continue;
      filesToLoad.push({
        path: join(
          config.directory,
          hookCfg.file.startsWith("./") ? hookCfg.file.slice(2) : hookCfg.file,
        ),
        priority: hookCfg.priority,
        config: hookCfg.config,
      });
    }
  }

  // Auto-discover
  if (config.autoDiscover !== false) {
    try {
      const entries = await readdir(config.directory);
      for (const entry of entries) {
        if (!isHookFile(entry)) continue;
        const fullPath = join(config.directory, entry);
        if (filesToLoad.some((f) => f.path === fullPath)) continue;
        filesToLoad.push({ path: fullPath });
      }
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
        log("warn", `failed to scan hooks directory: ${(err as Error).message}`, {
          dir: config.directory,
        });
      }
    }
  }

  for (const fileSpec of filesToLoad) {
    try {
      const mod = (await import(fileSpec.path)) as Record<string, unknown>;
      const createHook =
        (mod.default as ((...args: unknown[]) => unknown) | undefined) ??
        (mod.createHook as ((...args: unknown[]) => unknown) | undefined);
      if (typeof createHook !== "function") {
        result.failed.push({
          file: fileSpec.path,
          error: "no default export or createHook function",
        });
        continue;
      }
      const hookDef = createHook(sdk, fileSpec.config);
      const validationError = validateHookDefinition(hookDef, fileSpec.path);
      if (validationError) {
        result.failed.push({ file: fileSpec.path, error: validationError });
        continue;
      }
      const hook: RegisteredHook = {
        ...(hookDef as RegisteredHook),
        source: "custom" as const,
        enabled: true,
      };
      if (fileSpec.priority !== undefined) hook.priority = fileSpec.priority;
      registry.register(hook);
      result.loaded.push(hook.name);
      log("info", `loaded external hook: ${hook.name}`, {
        file: fileSpec.path,
        point: hook.point,
        priority: hook.priority,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      result.failed.push({ file: fileSpec.path, error: msg });
      log("warn", `failed to load hook from ${fileSpec.path}: ${msg}`);
    }
  }

  return result;
}

/** Test-only exports for unit test validation helpers. */
export const _test = {
  isValidHookPoint,
  isHookFile,
  validateHookDefinition,
};
