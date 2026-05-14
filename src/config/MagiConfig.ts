/**
 * MagiConfig — reads `magi.config.yaml` from the workspace root and
 * exposes typed hook / tool / classifier extension configuration.
 *
 * Singleton pattern: loaded once per process, cached. Falls back
 * gracefully when the config file does not exist (empty defaults).
 */

import fs from "node:fs";
import path from "node:path";
import { parse as parseYaml } from "yaml";

/* ------------------------------------------------------------------ */
/*  Public types                                                       */
/* ------------------------------------------------------------------ */

export interface HookOverride {
  enabled?: boolean;
  priority?: number;
  blocking?: boolean;
  timeoutMs?: number;
}

export interface HooksConfig {
  disable_builtin: string[];
  directory: string;
  global_directory: string;
  overrides: Record<string, HookOverride>;
}

export interface ToolOverrideConfig {
  enabled?: boolean;
  permission?: string;
  timeoutMs?: number;
}

export interface ToolsConfig {
  disable_builtin: string[];
  directory: string;
  global_directory: string;
  packages: string[];
  overrides: Record<string, ToolOverrideConfig>;
}

export interface CustomDimension {
  phase: "request" | "final_answer";
  prompt: string;
  output_schema: Record<string, string>;
}

export interface ClassifierConfig {
  custom_dimensions: Record<string, CustomDimension>;
}

export interface MagiConfigData {
  hooks: HooksConfig;
  tools: ToolsConfig;
  classifier: ClassifierConfig;
}

/* ------------------------------------------------------------------ */
/*  Defaults                                                           */
/* ------------------------------------------------------------------ */

const DEFAULT_HOOKS_DIR = "./hooks";
const DEFAULT_GLOBAL_HOOKS_DIR = "~/.magi/hooks";
const DEFAULT_TOOLS_DIR = "./tools";
const DEFAULT_GLOBAL_TOOLS_DIR = "~/.magi/tools";
const CONFIG_FILENAME = "magi.config.yaml";

function defaultConfig(): MagiConfigData {
  return {
    hooks: {
      disable_builtin: [],
      directory: DEFAULT_HOOKS_DIR,
      global_directory: DEFAULT_GLOBAL_HOOKS_DIR,
      overrides: {},
    },
    tools: {
      disable_builtin: [],
      directory: DEFAULT_TOOLS_DIR,
      global_directory: DEFAULT_GLOBAL_TOOLS_DIR,
      packages: [],
      overrides: {},
    },
    classifier: {
      custom_dimensions: {},
    },
  };
}

/* ------------------------------------------------------------------ */
/*  Env-var substitution                                               */
/* ------------------------------------------------------------------ */

function resolveEnvVars(obj: unknown): unknown {
  if (typeof obj === "string") {
    return obj.replace(/\$\{([^}]+)\}/g, (_match, varName: string) => {
      return process.env[varName.trim()] ?? "";
    });
  }
  if (Array.isArray(obj)) {
    return obj.map(resolveEnvVars);
  }
  if (obj !== null && typeof obj === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(
      obj as Record<string, unknown>,
    )) {
      result[key] = resolveEnvVars(value);
    }
    return result;
  }
  return obj;
}

/* ------------------------------------------------------------------ */
/*  Parser helpers                                                     */
/* ------------------------------------------------------------------ */

function parseHooksSection(raw: Record<string, unknown>): HooksConfig {
  const section = raw.hooks as Record<string, unknown> | undefined;
  if (!section || typeof section !== "object") {
    return defaultConfig().hooks;
  }

  const disableBuiltin = Array.isArray(section.disable_builtin)
    ? (section.disable_builtin as unknown[]).filter(
        (v): v is string => typeof v === "string",
      )
    : [];

  const directory =
    typeof section.directory === "string"
      ? section.directory
      : DEFAULT_HOOKS_DIR;

  const globalDirectory =
    typeof section.global_directory === "string"
      ? section.global_directory
      : DEFAULT_GLOBAL_HOOKS_DIR;

  const rawOverrides =
    section.overrides && typeof section.overrides === "object"
      ? (section.overrides as Record<string, unknown>)
      : {};

  const overrides: Record<string, HookOverride> = {};
  for (const [name, val] of Object.entries(rawOverrides)) {
    if (val && typeof val === "object") {
      const v = val as Record<string, unknown>;
      overrides[name] = {
        ...(typeof v.enabled === "boolean" ? { enabled: v.enabled } : {}),
        ...(typeof v.priority === "number" ? { priority: v.priority } : {}),
        ...(typeof v.blocking === "boolean" ? { blocking: v.blocking } : {}),
        ...(typeof v.timeoutMs === "number" ? { timeoutMs: v.timeoutMs } : {}),
      };
    }
  }

  return { disable_builtin: disableBuiltin, directory, global_directory: globalDirectory, overrides };
}

function parseToolsSection(raw: Record<string, unknown>): ToolsConfig {
  const section = raw.tools as Record<string, unknown> | undefined;
  if (!section || typeof section !== "object") {
    return defaultConfig().tools;
  }

  const disableBuiltin = Array.isArray(section.disable_builtin)
    ? (section.disable_builtin as unknown[]).filter(
        (v): v is string => typeof v === "string",
      )
    : [];

  const directory =
    typeof section.directory === "string"
      ? section.directory
      : DEFAULT_TOOLS_DIR;

  const globalDirectory =
    typeof section.global_directory === "string"
      ? section.global_directory
      : DEFAULT_GLOBAL_TOOLS_DIR;

  const packages = Array.isArray(section.packages)
    ? (section.packages as unknown[]).filter(
        (v): v is string => typeof v === "string",
      )
    : [];

  const rawOverrides =
    section.overrides && typeof section.overrides === "object"
      ? (section.overrides as Record<string, unknown>)
      : {};

  const overrides: Record<string, ToolOverrideConfig> = {};
  for (const [name, val] of Object.entries(rawOverrides)) {
    if (val && typeof val === "object") {
      const v = val as Record<string, unknown>;
      overrides[name] = {
        ...(typeof v.enabled === "boolean" ? { enabled: v.enabled } : {}),
        ...(typeof v.permission === "string"
          ? { permission: v.permission }
          : {}),
        ...(typeof v.timeoutMs === "number" ? { timeoutMs: v.timeoutMs } : {}),
      };
    }
  }

  return {
    disable_builtin: disableBuiltin,
    directory,
    global_directory: globalDirectory,
    packages,
    overrides,
  };
}

function parseClassifierSection(
  raw: Record<string, unknown>,
): ClassifierConfig {
  const section = raw.classifier as Record<string, unknown> | undefined;
  if (!section || typeof section !== "object") {
    return defaultConfig().classifier;
  }

  const rawDims =
    section.custom_dimensions && typeof section.custom_dimensions === "object"
      ? (section.custom_dimensions as Record<string, unknown>)
      : {};

  const customDimensions: Record<string, CustomDimension> = {};
  for (const [name, val] of Object.entries(rawDims)) {
    if (val && typeof val === "object") {
      const v = val as Record<string, unknown>;
      const phase = v.phase;
      if (phase !== "request" && phase !== "final_answer") continue;
      if (typeof v.prompt !== "string") continue;

      const rawSchema =
        v.output_schema && typeof v.output_schema === "object"
          ? (v.output_schema as Record<string, unknown>)
          : {};
      const outputSchema: Record<string, string> = {};
      for (const [k, sv] of Object.entries(rawSchema)) {
        if (typeof sv === "string") outputSchema[k] = sv;
      }

      customDimensions[name] = {
        phase,
        prompt: v.prompt as string,
        output_schema: outputSchema,
      };
    }
  }

  return { custom_dimensions: customDimensions };
}

/* ------------------------------------------------------------------ */
/*  Singleton                                                          */
/* ------------------------------------------------------------------ */

let cachedConfig: MagiConfigData | null = null;
let cachedDir: string | null = null;

export function loadMagiConfig(dir?: string): MagiConfigData {
  const resolvedDir = dir ?? process.cwd();
  if (cachedConfig && cachedDir === resolvedDir) return cachedConfig;

  const configPath = path.join(resolvedDir, CONFIG_FILENAME);

  let raw: string;
  try {
    raw = fs.readFileSync(configPath, "utf-8");
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      cachedConfig = defaultConfig();
      cachedDir = resolvedDir;
      return cachedConfig;
    }
    throw new Error(
      `Failed to read ${CONFIG_FILENAME}: ${(err as Error).message}`,
    );
  }

  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch (err) {
    throw new Error(
      `Invalid YAML in ${configPath}: ${(err as Error).message}`,
    );
  }

  if (!parsed || typeof parsed !== "object") {
    cachedConfig = defaultConfig();
    cachedDir = resolvedDir;
    return cachedConfig;
  }

  const resolved = resolveEnvVars(parsed) as Record<string, unknown>;

  cachedConfig = {
    hooks: parseHooksSection(resolved),
    tools: parseToolsSection(resolved),
    classifier: parseClassifierSection(resolved),
  };
  cachedDir = resolvedDir;
  return cachedConfig;
}

export function resetMagiConfig(): void {
  cachedConfig = null;
  cachedDir = null;
}

export function magiConfigPath(dir?: string): string {
  return path.join(dir ?? process.cwd(), CONFIG_FILENAME);
}
