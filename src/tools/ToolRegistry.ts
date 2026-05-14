/**
 * ToolRegistry — in-memory tool dispatch.
 * Design reference: §5.4.
 *
 * Phase 1b: plain register + resolve. Skills HTTP-pull integration
 * (§9.8) lands alongside skill loading in Phase 2.
 */

import type { Tool, ToolRegistry as IToolRegistry } from "../Tool.js";
import {
  loadSkillsFromDir,
  type SkillLoadReport,
} from "./SkillLoader.js";

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

export class ToolRegistry implements IToolRegistry {
  private readonly tools = new Map<string, Tool>();
  /** Last loadSkills() result — exposed via /healthz. */
  private lastSkillReport: SkillLoadReport | null = null;
  private readonly loadedSkillToolNames = new Set<string>();
  private currentMode: RegistryMode = "act";

  register(tool: Tool): void {
    if (this.tools.has(tool.name)) {
      throw new Error(`tool already registered: ${tool.name}`);
    }
    this.tools.set(tool.name, tool);
  }

  /** Remove a tool by name. Returns true if it existed. */
  unregister(name: string): boolean {
    return this.tools.delete(name);
  }

  /** Replace an existing registration — used during skill hot-reload. */
  replace(tool: Tool): void {
    this.tools.set(tool.name, tool);
  }

  resolve(name: string): Tool | null {
    return this.tools.get(name) ?? null;
  }

  list(): Tool[] {
    return [...this.tools.values()];
  }

  setMode(mode: RegistryMode): void {
    this.currentMode = mode;
  }

  getMode(): RegistryMode {
    return this.currentMode;
  }

  getAvailableTools(): Tool[] {
    const mode = this.currentMode;
    return [...this.tools.values()].filter((tool) => {
      const modes = tool.availableInModes ?? inferModes(tool);
      return modes.includes(mode);
    });
  }

  isToolAllowedInCurrentMode(name: string): boolean {
    const tool = this.tools.get(name);
    if (!tool) return false;
    const modes = tool.availableInModes ?? inferModes(tool);
    return modes.includes(this.currentMode);
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
      if (current?.kind === "skill") {
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
        this.tools.set(t.name, t);
        this.loadedSkillToolNames.add(t.name);
      }
    }

    combinedReport.loaded = [...loadedByName.values()];
    this.lastSkillReport = combinedReport;
    return combinedReport.loaded.length;
  }

  skillReport(): SkillLoadReport | null {
    return this.lastSkillReport;
  }
}
