/**
 * SkillLoader bundled-superpowers discovery test.
 *
 * Ensures the 14 skills copied into
 *   infra/docker/clawy-core-agent/skills/superpowers/
 * all parse via the Phase 2b prompt-only loader (they ship with no
 * `input_schema` / `entry`, so inference should route them to
 * `kind: prompt`).
 *
 * See docs/plans/2026-04-20-superpowers-plugin-design.md.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { describe, it, expect } from "vitest";
import { loadSkillsFromDir } from "./SkillLoader.js";

const SUPERPOWERS_ROOT = path.resolve(
  process.cwd(),
  "skills",
  "superpowers",
);

describe("SkillLoader + bundled superpowers", () => {
  it("discovers all 14 skills as prompt-only tools", async () => {
    let present = true;
    try {
      await fs.access(SUPERPOWERS_ROOT);
    } catch {
      present = false;
    }
    // When running inside the Docker image or repo, the bundled dir
    // exists. If somehow absent (e.g. partial checkout), the test
    // short-circuits with a skip marker — it would mask an actual
    // regression otherwise.
    expect(present).toBe(true);

    const { tools, report } = await loadSkillsFromDir({
      skillsDir: SUPERPOWERS_ROOT,
      workspaceRoot: SUPERPOWERS_ROOT,
    });

    expect(tools).toHaveLength(14);
    // Every entry should have been classified as prompt-only.
    expect(report.loaded).toHaveLength(14);
    for (const entry of report.loaded) {
      expect(entry.promptOnly).toBe(true);
      expect(entry.scriptBacked).toBe(false);
    }
    // And no issues.
    expect(report.issues).toEqual([]);
  });

  it("every bundled skill surfaces a canonical `name` frontmatter", async () => {
    const { tools } = await loadSkillsFromDir({
      skillsDir: SUPERPOWERS_ROOT,
      workspaceRoot: SUPERPOWERS_ROOT,
    });
    const names = tools.map((t) => t.name).sort();
    const expected = [
      "brainstorming",
      "dispatching-parallel-agents",
      "executing-plans",
      "finishing-a-development-branch",
      "receiving-code-review",
      "requesting-code-review",
      "subagent-driven-development",
      "systematic-debugging",
      "test-driven-development",
      "using-git-worktrees",
      "using-superpowers",
      "verification-before-completion",
      "writing-plans",
      "writing-skills",
    ].sort();
    expect(names).toEqual(expected);
  });

  it("no two bundled skills collide on name", async () => {
    const { tools } = await loadSkillsFromDir({
      skillsDir: SUPERPOWERS_ROOT,
      workspaceRoot: SUPERPOWERS_ROOT,
    });
    const unique = new Set(tools.map((t) => t.name));
    expect(unique.size).toBe(tools.length);
  });
});
