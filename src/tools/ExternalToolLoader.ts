/**
 * ExternalToolLoader — discover and load external tools from workspace
 * directories. Each tool lives in its own subdirectory with a
 * `tool.config.yaml` descriptor and an `index.mjs` ESM module.
 */

import { readdir, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { parse as parseYaml } from "yaml";
import type { Tool, ToolContext, ToolResult, PermissionClass } from "../Tool.js";

const TOOL_CONFIG_FILE = "tool.config.yaml";
const TOOL_INDEX_FILE = "index.mjs";

export interface ExternalToolSdk {
  readonly version: string;
}

export type TrustLevel = "sandboxed" | "workspace" | "full";

export interface ExternalToolConfig {
  name: string;
  description: string;
  inputSchema: object;
  permission: PermissionClass;
  tags?: string[];
  isConcurrencySafe?: boolean;
  dangerous?: boolean;
  shouldDefer?: boolean;
  trustLevel?: TrustLevel;
}

interface ExternalToolContext {
  readonly botId: string;
  readonly sessionKey: string;
  readonly turnId: string;
  readonly workspaceRoot: string;
  readonly abortSignal: AbortSignal;
  readonly emitProgress: ToolContext["emitProgress"];
}

export interface LoadResult {
  loaded: string[];
  failed: Array<{ dir: string; error: string }>;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function createSdk(): ExternalToolSdk {
  return Object.freeze({ version: "1.0.0" });
}

function adaptContext(full: ToolContext): ExternalToolContext {
  return {
    botId: full.botId,
    sessionKey: full.sessionKey,
    turnId: full.turnId,
    workspaceRoot: full.workspaceRoot,
    abortSignal: full.abortSignal,
    emitProgress: full.emitProgress,
  };
}

const VALID_PERMISSIONS = new Set<string>(["read", "write", "execute", "net", "meta"]);

function parseToolConfig(
  raw: string,
  dir: string,
): { config: ExternalToolConfig; error?: undefined } | { config?: undefined; error: string } {
  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch (err) {
    return { error: `invalid YAML in ${dir}/${TOOL_CONFIG_FILE}: ${(err as Error).message}` };
  }
  if (!isRecord(parsed)) return { error: `${dir}/${TOOL_CONFIG_FILE} must be an object` };

  const name = typeof parsed.name === "string" ? parsed.name.trim() : "";
  if (!name) return { error: `${dir}/${TOOL_CONFIG_FILE}: missing name` };

  const description = typeof parsed.description === "string" ? parsed.description.trim() : "";
  if (!description) return { error: `${dir}/${TOOL_CONFIG_FILE}: missing description` };

  const permission = typeof parsed.permission === "string" ? parsed.permission : "read";
  if (!VALID_PERMISSIONS.has(permission)) {
    return { error: `${dir}/${TOOL_CONFIG_FILE}: invalid permission "${permission}"` };
  }

  const inputSchema = isRecord(parsed.inputSchema ?? parsed.input_schema)
    ? ((parsed.inputSchema ?? parsed.input_schema) as object)
    : { type: "object", properties: {} };

  return {
    config: {
      name,
      description,
      inputSchema,
      permission: permission as PermissionClass,
      tags: Array.isArray(parsed.tags)
        ? parsed.tags.filter((t): t is string => typeof t === "string")
        : undefined,
      isConcurrencySafe:
        typeof parsed.isConcurrencySafe === "boolean" ? parsed.isConcurrencySafe : undefined,
      dangerous: typeof parsed.dangerous === "boolean" ? parsed.dangerous : undefined,
      shouldDefer: typeof parsed.shouldDefer === "boolean" ? parsed.shouldDefer : undefined,
      trustLevel:
        typeof parsed.trustLevel === "string" &&
        (parsed.trustLevel === "sandboxed" ||
          parsed.trustLevel === "workspace" ||
          parsed.trustLevel === "full")
          ? (parsed.trustLevel as TrustLevel)
          : "sandboxed",
    },
  };
}

export async function loadExternalTools(
  dirs: string[],
  trustedDirs: string[],
  log: (level: "info" | "warn" | "error", msg: string, data?: Record<string, unknown>) => void,
): Promise<{ tools: Tool[]; result: LoadResult }> {
  const sdk = createSdk();
  const result: LoadResult = { loaded: [], failed: [] };
  const tools: Tool[] = [];
  const trustedSet = new Set(trustedDirs.map((d) => resolve(d)));

  for (const baseDir of dirs) {
    let entries: string[];
    try {
      entries = await readdir(baseDir);
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
        log("warn", `failed to scan tools dir: ${(err as Error).message}`, { dir: baseDir });
      }
      continue;
    }

    for (const entry of entries) {
      const toolDir = join(baseDir, entry);
      try {
        const configRaw = await readFile(join(toolDir, TOOL_CONFIG_FILE), "utf8");
        const configResult = parseToolConfig(configRaw, toolDir);
        if (configResult.error || !configResult.config) {
          result.failed.push({ dir: toolDir, error: configResult.error ?? "unknown" });
          continue;
        }
        const config = configResult.config;

        // Permission check: execute requires trusted dir
        if (config.permission === "execute" && !trustedSet.has(resolve(toolDir))) {
          result.failed.push({ dir: toolDir, error: "execute permission requires trusted dir" });
          continue;
        }

        const modulePath = join(toolDir, TOOL_INDEX_FILE);
        const mod = (await import(modulePath)) as Record<string, unknown>;
        const factory = (mod.default ?? mod.createTool) as
          | ((sdk: ExternalToolSdk, config: ExternalToolConfig) => unknown)
          | undefined;
        if (typeof factory !== "function") {
          result.failed.push({ dir: toolDir, error: "no default export or createTool function" });
          continue;
        }

        const impl = factory(sdk, config) as
          | { execute: (input: unknown, ctx: unknown) => Promise<unknown> }
          | null
          | undefined;
        if (!impl || typeof impl.execute !== "function") {
          result.failed.push({
            dir: toolDir,
            error: "factory did not return an object with execute()",
          });
          continue;
        }

        const tool: Tool = {
          name: config.name,
          description: config.description,
          inputSchema: config.inputSchema,
          permission: config.permission,
          kind: "external",
          tags: config.tags,
          isConcurrencySafe: config.isConcurrencySafe,
          dangerous: config.dangerous,
          shouldDefer: config.shouldDefer,
          execute: async (input: unknown, ctx: ToolContext): Promise<ToolResult> => {
            const extCtx = config.trustLevel === "full" ? ctx : adaptContext(ctx);
            const startMs = Date.now();
            try {
              const output = await impl.execute(input, extCtx);
              return {
                status: "ok",
                output,
                durationMs: Date.now() - startMs,
              };
            } catch (err) {
              return {
                status: "error",
                errorMessage: err instanceof Error ? err.message : String(err),
                durationMs: Date.now() - startMs,
              };
            }
          },
        };

        tools.push(tool);
        result.loaded.push(config.name);
        log("info", `loaded external tool: ${config.name}`, {
          dir: toolDir,
          permission: config.permission,
        });
      } catch (err) {
        result.failed.push({
          dir: toolDir,
          error: err instanceof Error ? err.message : String(err),
        });
        log("warn", `failed to load tool from ${toolDir}: ${(err as Error).message}`);
      }
    }
  }

  return { tools, result };
}
