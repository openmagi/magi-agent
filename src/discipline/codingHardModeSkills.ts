import type { ToolRegistry } from "../Tool.js";

const CODING_HARD_MODE_SKILL_NAMES = new Set([
  "coding-agent",
  "complex-coding",
]);

export function isCodingHardModeSkillActive(
  tools: Pick<ToolRegistry, "resolve">,
): boolean {
  for (const name of CODING_HARD_MODE_SKILL_NAMES) {
    const tool = tools.resolve(name);
    if (tool?.kind === "skill") return true;
  }
  return false;
}
