/**
 * AgentConfigFile — reads `agent.config.yaml` from workspace root.
 * Provides per-workspace tool configuration: disabled tools, tool config,
 * external tool directories, trusted directories, and per-turn limits.
 */

import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { parse as parseYaml } from "yaml";

const AGENT_CONFIG_REL = "agent.config.yaml";

export interface ToolsConfig {
  disabled: string[];
  config: Record<string, Record<string, unknown>>;
  externalDirs: string[];
  trustedDirs: string[];
  maxToolsPerTurn: number;
}

export interface AgentConfigFile {
  tools: ToolsConfig;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

const DEFAULT_TOOLS_CONFIG: ToolsConfig = {
  disabled: [],
  config: {},
  externalDirs: [],
  trustedDirs: [],
  maxToolsPerTurn: 50,
};

export async function loadAgentConfigFile(
  workspaceRoot: string,
): Promise<{ config: AgentConfigFile; warnings: string[] }> {
  const warnings: string[] = [];
  const config: AgentConfigFile = { tools: { ...DEFAULT_TOOLS_CONFIG } };

  let raw: string;
  try {
    raw = await readFile(join(workspaceRoot, AGENT_CONFIG_REL), "utf8");
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return { config, warnings };
    warnings.push(`failed to read ${AGENT_CONFIG_REL}: ${(err as Error).message}`);
    return { config, warnings };
  }

  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch (err) {
    warnings.push(`failed to parse ${AGENT_CONFIG_REL}: ${(err as Error).message}`);
    return { config, warnings };
  }
  if (!isRecord(parsed)) return { config, warnings };

  const toolsSection = parsed.tools;
  if (!isRecord(toolsSection)) return { config, warnings };

  // disabled
  const disabled = toolsSection.disabled;
  if (Array.isArray(disabled)) {
    config.tools.disabled = disabled.filter((d): d is string => typeof d === "string" && d.trim().length > 0);
  }

  // config
  const toolConfig = toolsSection.config;
  if (isRecord(toolConfig)) {
    for (const [key, value] of Object.entries(toolConfig)) {
      if (isRecord(value)) {
        config.tools.config[key] = value as Record<string, unknown>;
      }
    }
  }

  // externalDirs
  const externalDirs = toolsSection.externalDirs ?? toolsSection.external_dirs;
  if (Array.isArray(externalDirs)) {
    config.tools.externalDirs = externalDirs.filter((d): d is string => typeof d === "string");
  }

  // trustedDirs
  const trustedDirs = toolsSection.trustedDirs ?? toolsSection.trusted_dirs;
  if (Array.isArray(trustedDirs)) {
    config.tools.trustedDirs = trustedDirs.filter((d): d is string => typeof d === "string");
  }

  // maxToolsPerTurn
  const maxTools = toolsSection.maxToolsPerTurn ?? toolsSection.max_tools_per_turn;
  if (typeof maxTools === "number" && Number.isFinite(maxTools)) {
    config.tools.maxToolsPerTurn = Math.min(100, Math.max(1, Math.trunc(maxTools)));
  }

  return { config, warnings };
}
