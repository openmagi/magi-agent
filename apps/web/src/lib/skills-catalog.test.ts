import { describe, expect, it } from "vitest";
import { SKILLS } from "./skills-catalog";

describe("OSS skills catalog", () => {
  it("only exposes generic bundled skills to the self-hosted UI", () => {
    const ids = SKILLS.map((skill) => skill.id);

    expect(ids).toEqual([
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
    ]);
    expect(ids).not.toContain("google-ads");
    expect(ids).not.toContain("trading");
    expect(ids).not.toContain("restaurant");
    expect(ids).not.toContain("korean-corporate-disclosure");
  });
});
