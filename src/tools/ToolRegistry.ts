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

export class ToolRegistry implements IToolRegistry {
  private readonly tools = new Map<string, Tool>();
  /** Last loadSkills() result — exposed via /healthz. */
  private lastSkillReport: SkillLoadReport | null = null;

  register(tool: Tool): void {
    if (this.tools.has(tool.name)) {
      throw new Error(`tool already registered: ${tool.name}`);
    }
    this.tools.set(tool.name, tool);
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

  /**
   * Walk a workspace-scoped skills directory and register every valid
   * skill as a Tool. Returns the number of skills loaded; the detailed
   * report (including lint failures) is exposed via skillReport().
   */
  async loadSkills(
    skillsDir: string,
    workspaceRoot?: string,
    opts: {
      trustedSkillRoots?: readonly string[];
      trustedSkillDirs?: readonly string[];
    } = {},
  ): Promise<number> {
    const { tools, report } = await loadSkillsFromDir({
      skillsDir,
      workspaceRoot: workspaceRoot ?? skillsDir,
      trustedSkillRoots: opts.trustedSkillRoots,
      trustedSkillDirs: opts.trustedSkillDirs,
    });
    for (const t of tools) {
      // Skills can overlap with bot-native tool names — skills win on
      // conflict (bot author's intent).
      this.tools.set(t.name, t);
    }
    this.lastSkillReport = report;
    return tools.length;
  }

  skillReport(): SkillLoadReport | null {
    return this.lastSkillReport;
  }
}
