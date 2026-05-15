import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

function read(relPath: string): string {
  return readFileSync(path.join(process.cwd(), relPath), "utf8");
}

describe("coding sandbox harness templates", () => {
  it("requires complex coding work to start in a dedicated project sandbox", () => {
    const skill = read("src/lib/templates/skills/complex-coding/SKILL.md");

    expect(skill).toContain("Project Sandbox Protocol");
    expect(skill).toContain("/workspace/code/");
    expect(skill).toContain("git init");
    expect(skill).toContain(".sandbox.json");
    expect(skill).toContain("No Docker-in-Docker");
    expect(skill).toContain("/var/run/docker.sock");
    expect(skill).toContain("TestRun");
    expect(skill).toContain("GitDiff");
  });

  it("keeps coding standards aligned with the sandbox boundary", () => {
    const standards = read("src/lib/templates/skills/coding-standards/SKILL.md");

    expect(standards).toContain("Project Sandbox Protocol");
    expect(standards).toContain("CodeWorkspace");
    expect(standards).toContain("/workspace/code/");
    expect(standards).toContain("No Docker-in-Docker");
    expect(standards).toContain("privileged containers");
  });

  it("documents the same boundary for subagents and environment self-knowledge", () => {
    const execution = read("src/lib/templates/static/EXECUTION.md");
    const executionTools = read("src/lib/templates/static/EXECUTION-TOOLS.md");
    const meta = read("src/lib/templates/skills/meta-cognition/SKILL.md");

    expect(execution).toContain("sandbox first");
    expect(execution).toContain("/workspace/code/");
    expect(executionTools).toContain("Project sandbox");
    expect(executionTools).toContain("/workspace/code/");
    expect(meta).toContain("Project sandbox protocol");
    expect(meta).toContain("No Docker-in-Docker");
  });
});
