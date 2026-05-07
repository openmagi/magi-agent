/**
 * Config file loader — reads magi-agent.yaml from the working directory,
 * resolves ${ENV_VAR} references, and returns a typed config object.
 */

import fs from "node:fs";
import path from "node:path";
import { parse as parseYaml } from "yaml";
import type { ModelCapabilityOverride } from "../config/registerConfiguredModelCapability.js";

export interface MagiAgentConfig {
  llm: {
    provider: "anthropic" | "openai" | "google" | "openai-compatible";
    model: string;
    apiKey?: string;
    baseUrl?: string;
    capabilities?: ModelCapabilityOverride;
  };
  channels?: {
    telegram?: { token: string };
    discord?: { token: string };
    webhook?: { url: string; secret?: string };
  };
  hooks?: {
    builtin?: Record<string, boolean>;
    custom?: Array<{ path: string; event: string; priority: number }>;
  };
  memory?: {
    enabled?: boolean;
    compaction?: boolean;
  };
  server?: {
    /**
     * HTTP bearer token for local/self-hosted API access. Keep this separate
     * from llm.apiKey so browser clients never need the provider secret.
     */
    gatewayToken?: string;
  };
  workspace?: string;
  identity?: {
    name?: string;
    instructions?: string;
  };
}

const CONFIG_FILENAME = "magi-agent.yaml";
const CONFIG_PROVIDERS = ["anthropic", "openai", "google", "openai-compatible"] as const;

/**
 * Recursively walk a parsed YAML object and replace every `${VAR_NAME}`
 * occurrence in string values with `process.env.VAR_NAME`. Unresolved
 * references (env var not set) are replaced with an empty string.
 */
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
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      result[key] = resolveEnvVars(value);
    }
    return result;
  }
  return obj;
}

function validateCapabilityNumber(
  capabilities: Record<string, unknown>,
  field: keyof ModelCapabilityOverride,
  configPath: string,
): void {
  const value = capabilities[field];
  if (value === undefined) return;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new Error(`Invalid llm.capabilities.${field} in ${configPath}.`);
  }
}

function validateCapabilities(
  llm: Record<string, unknown>,
  configPath: string,
): void {
  if (llm.capabilities === undefined) return;
  if (
    !llm.capabilities ||
    typeof llm.capabilities !== "object" ||
    Array.isArray(llm.capabilities)
  ) {
    throw new Error(`Invalid llm.capabilities in ${configPath}.`);
  }
  const capabilities = llm.capabilities as Record<string, unknown>;
  if (
    capabilities.supportsThinking !== undefined &&
    typeof capabilities.supportsThinking !== "boolean"
  ) {
    throw new Error(`Invalid llm.capabilities.supportsThinking in ${configPath}.`);
  }
  validateCapabilityNumber(capabilities, "maxOutputTokens", configPath);
  validateCapabilityNumber(capabilities, "contextWindow", configPath);
  validateCapabilityNumber(capabilities, "inputUsdPerMtok", configPath);
  validateCapabilityNumber(capabilities, "outputUsdPerMtok", configPath);
}

/**
 * Load and parse `magi-agent.yaml` from the given directory (defaults
 * to `process.cwd()`). Throws with a user-friendly message if the file
 * is missing or malformed.
 */
export function loadConfig(dir?: string): MagiAgentConfig {
  const configPath = path.join(dir ?? process.cwd(), CONFIG_FILENAME);

  let raw: string;
  try {
    raw = fs.readFileSync(configPath, "utf-8");
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      throw new Error(
        `Config file not found: ${configPath}\nRun "magi-agent init" to create one.`,
      );
    }
    throw new Error(`Failed to read config: ${(err as Error).message}`);
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
    throw new Error(`Config file is empty or not an object: ${configPath}`);
  }

  const resolved = resolveEnvVars(parsed) as Record<string, unknown>;

  // Minimal validation — ensure the llm block exists.
  const llm = resolved.llm as Record<string, unknown> | undefined;
  if (!llm || typeof llm !== "object") {
    throw new Error(
      `Missing "llm" section in ${configPath}. Run "magi-agent init" to generate a valid config.`,
    );
  }

  const provider = llm.provider as string;
  if (!CONFIG_PROVIDERS.includes(provider as typeof CONFIG_PROVIDERS[number])) {
    throw new Error(
      `Invalid llm.provider "${provider}" — must be anthropic, openai, google, or openai-compatible.`,
    );
  }

  if (!llm.model || typeof llm.model !== "string") {
    throw new Error(`Missing llm.model in ${configPath}.`);
  }

  const hasApiKey =
    typeof llm.apiKey === "string" && llm.apiKey.trim().length > 0;
  if (provider !== "openai-compatible" && !hasApiKey) {
    throw new Error(
      `Missing llm.apiKey in ${configPath}. Set the environment variable or provide the key directly.`,
    );
  }

  if (
    provider === "openai-compatible" &&
    (typeof llm.baseUrl !== "string" || llm.baseUrl.trim().length === 0)
  ) {
    throw new Error(
      `Missing llm.baseUrl in ${configPath}. OpenAI-compatible local providers require a base URL.`,
    );
  }

  if (llm.baseUrl !== undefined && typeof llm.baseUrl !== "string") {
    throw new Error(`Invalid llm.baseUrl in ${configPath}.`);
  }

  validateCapabilities(llm, configPath);

  return resolved as unknown as MagiAgentConfig;
}

/** Return the path where the config file would be written. */
export function configFilePath(dir?: string): string {
  return path.join(dir ?? process.cwd(), CONFIG_FILENAME);
}
