/**
 * ToolRegistry — in-memory tool dispatch.
 * Design reference: §5.4.
 *
 * Phase 1b: plain register + resolve. Skills HTTP-pull integration
 * (§9.8) lands alongside skill loading in Phase 2.
 *
 * Phase 1 custom tools: ToolEntry with source/enabled/stats tracking,
 * external tool management (disable/enable/listAll/getToolStats/recordExecution).
 */

import type { Tool, ToolRegistry as IToolRegistry } from "../Tool.js";
import {
  loadSkillsFromDir,
  type SkillLoadReport,
} from "./SkillLoader.js";

export interface ToolStats {
  calls: number;
  errors: number;
  avgDurationMs: number;
  lastCallAt: number;
}

export interface ToolEntry {
  tool: Tool;
  enabled: boolean;
  source: "builtin" | "skill" | "external";
  registeredAt: number;
  stats: ToolStats;
}

export interface ToolMetadata {
  name: string;
  description: string;
  permission: string;
  kind: string;
  enabled: boolean;
  source: "builtin" | "skill" | "external";
  isConcurrencySafe: boolean;
  dangerous: boolean;
  tags: string[];
  stats: ToolStats;
}

export interface SkillRoot {
  skillsDir: string;
  workspaceRoot?: string;
}

export interface SkillLoadOptions {
  trustedSkillRoots?: readonly string[];
  trustedSkillDirs?: readonly string[];
}

export type RegistryMode = "plan" | "act";

const DEFAULT_MODES: readonly ("plan" | "act")[] = ["plan", "act"];
const ACT_ONLY_PERMISSIONS = new Set(["write", "execute"]);

function inferModes(tool: Tool): readonly ("plan" | "act")[] {
  if (ACT_ONLY_PERMISSIONS.has(tool.permission) || tool.mutatesWorkspace) {
    return ["act"];
  }
  return DEFAULT_MODES;
}

function deriveSource(tool: Tool): "builtin" | "skill" | "external" {
  if (tool.kind === "skill") return "skill";
  if (tool.kind === "external") return "external";
  return "builtin";
}

function makeDefaultStats(): ToolStats {
  return { calls: 0, errors: 0, avgDurationMs: 0, lastCallAt: 0 };
}

export class ToolRegistry implements IToolRegistry {
  private readonly tools = new Map<string, ToolEntry>();
  /** Last loadSkills() result — exposed via /healthz. */
  private lastSkillReport: SkillLoadReport | null = null;
  private readonly loadedSkillToolNames = new Set<string>();
  private currentMode: RegistryMode = "act";

  register(tool: Tool): void {
    if (this.tools.has(tool.name)) {
      throw new Error(`tool already registered: ${tool.name}`);
    }
    this.tools.set(tool.name, {
      tool,
      enabled: true,
      source: deriveSource(tool),
      registeredAt: Date.now(),
      stats: makeDefaultStats(),
    });
  }

  /** Replace an existing registration — used during skill hot-reload. */
  replace(tool: Tool): void {
    const existing = this.tools.get(tool.name);
    const stats = existing ? { ...existing.stats } : makeDefaultStats();
    this.tools.set(tool.name, {
      tool,
      enabled: existing ? existing.enabled : true,
      source: deriveSource(tool),
      registeredAt: Date.now(),
      stats,
    });
  }

  resolve(name: string): Tool | null {
    const entry = this.tools.get(name);
    if (!entry || !entry.enabled) return null;
    return entry.tool;
  }

  list(): Tool[] {
    return [...this.tools.values()]
      .filter((e) => e.enabled)
      .map((e) => e.tool);
  }

  /**
   * Walk a workspace-scoped skills directory and register every valid
   * skill as a Tool. Returns the number of skills loaded; the detailed
   * report (including lint failures) is exposed via skillReport().
   */
  async loadSkills(
    skillsDir: string,
    workspaceRoot?: string,
    opts: SkillLoadOptions = {},
  ): Promise<number> {
    return this.loadSkillRoots(
      [{ skillsDir, workspaceRoot: workspaceRoot ?? skillsDir }],
      opts,
    );
  }

  /**
   * Load several skill roots as one hot-reload unit. Later roots win
   * name collisions, which lets bundled operator skills override stale
   * workspace copies while preserving unrelated workspace skills.
   */
  async loadSkillRoots(
    roots: readonly SkillRoot[],
    opts: SkillLoadOptions = {},
  ): Promise<number> {
    for (const name of this.loadedSkillToolNames) {
      const current = this.tools.get(name);
      if (current?.tool.kind === "skill") {
        this.tools.delete(name);
      }
    }
    this.loadedSkillToolNames.clear();

    const combinedReport: SkillLoadReport = {
      loaded: [],
      issues: [],
      runtimeHooks: [],
    };
    const loadedByName = new Map<string, SkillLoadReport["loaded"][number]>();

    for (const root of roots) {
      const { tools, report } = await loadSkillsFromDir({
        skillsDir: root.skillsDir,
        workspaceRoot: root.workspaceRoot ?? root.skillsDir,
        trustedSkillRoots: opts.trustedSkillRoots,
        trustedSkillDirs: opts.trustedSkillDirs,
      });

      combinedReport.issues.push(...report.issues);
      combinedReport.runtimeHooks.push(...report.runtimeHooks);
      for (const entry of report.loaded) {
        loadedByName.set(entry.name, entry);
      }

      for (const t of tools) {
        // Skills can overlap with bot-native tool names — skills win on
        // conflict (bot author's intent). Across skill roots, later roots
        // win so bundled operator skills can replace stale copies.
        this.tools.set(t.name, {
          tool: t,
          enabled: true,
          source: "skill",
          registeredAt: Date.now(),
          stats: makeDefaultStats(),
        });
        this.loadedSkillToolNames.add(t.name);
      }
    }

    combinedReport.loaded = [...loadedByName.values()];
    this.lastSkillReport = combinedReport;
    return combinedReport.loaded.length;
  }

  setMode(mode: RegistryMode): void {
    this.currentMode = mode;
  }

  getMode(): RegistryMode {
    return this.currentMode;
  }

  getAvailableTools(): Tool[] {
    const mode = this.currentMode;
    return [...this.tools.values()]
      .filter((e) => {
        if (!e.enabled) return false;
        const modes = e.tool.availableInModes ?? inferModes(e.tool);
        return modes.includes(mode);
      })
      .map((e) => e.tool);
  }

  isToolAllowedInCurrentMode(name: string): boolean {
    const entry = this.tools.get(name);
    if (!entry || !entry.enabled) return false;
    const modes = entry.tool.availableInModes ?? inferModes(entry.tool);
    return modes.includes(this.currentMode);
  }

  skillReport(): SkillLoadReport | null {
    return this.lastSkillReport;
  }

  // --- Custom tools: ToolEntry management ---

  /** Remove a non-builtin tool. Returns false for builtin or missing tools. */
  unregister(name: string): boolean {
    const entry = this.tools.get(name);
    if (!entry || entry.source === "builtin") return false;
    this.tools.delete(name);
    return true;
  }

  disable(name: string): boolean {
    const entry = this.tools.get(name);
    if (!entry) return false;
    entry.enabled = false;
    return true;
  }

  enable(name: string): boolean {
    const entry = this.tools.get(name);
    if (!entry) return false;
    entry.enabled = true;
    return true;
  }

  listAll(): ToolMetadata[] {
    return [...this.tools.values()].map((entry) => ({
      name: entry.tool.name,
      description: entry.tool.description,
      permission: entry.tool.permission,
      kind: entry.tool.kind ?? "core",
      enabled: entry.enabled,
      source: entry.source,
      isConcurrencySafe: entry.tool.isConcurrencySafe ?? false,
      dangerous: entry.tool.dangerous ?? false,
      tags: entry.tool.tags ?? [],
      stats: { ...entry.stats },
    }));
  }

  getToolStats(): Map<string, ToolStats> {
    const result = new Map<string, ToolStats>();
    for (const [name, entry] of this.tools) {
      result.set(name, { ...entry.stats });
    }
    return result;
  }

  recordExecution(name: string, durationMs: number, status: string): void {
    const entry = this.tools.get(name);
    if (!entry) return;
    entry.stats.calls++;
    entry.stats.lastCallAt = Date.now();
    if (status === "error") entry.stats.errors++;
    const prev = entry.stats.avgDurationMs;
    entry.stats.avgDurationMs = prev === 0
      ? durationMs
      : Math.round((prev * (entry.stats.calls - 1) + durationMs) / entry.stats.calls);
  }
}
