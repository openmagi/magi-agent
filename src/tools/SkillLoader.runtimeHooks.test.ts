import { describe, it, expect, beforeEach, afterEach } from "vitest";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { HookRegistry } from "../hooks/HookRegistry.js";
import type { HookContext } from "../hooks/types.js";
import { loadSkillsFromDir } from "./SkillLoader.js";
import { registerSkillRuntimeHooks } from "./SkillRuntimeHooks.js";
import type { LLMClient } from "../transport/LLMClient.js";

async function writeSkill(
  skillsDir: string,
  name: string,
  skillMd: string,
): Promise<string> {
  const skillDir = path.join(skillsDir, name);
  await fs.mkdir(skillDir, { recursive: true });
  await fs.writeFile(path.join(skillDir, "SKILL.md"), skillMd, "utf8");
  return skillDir;
}

async function writeHook(
  skillDir: string,
  rel: string,
  body = "#!/bin/sh\nexit 0\n",
): Promise<string> {
  const full = path.join(skillDir, rel);
  await fs.mkdir(path.dirname(full), { recursive: true });
  await fs.writeFile(full, body, { encoding: "utf8", mode: 0o755 });
  await fs.chmod(full, 0o755);
  return full;
}

async function sha256File(full: string): Promise<string> {
  return crypto.createHash("sha256").update(await fs.readFile(full)).digest("hex");
}

async function writeManifest(
  skillDir: string,
  files: Record<string, string>,
): Promise<void> {
  await fs.writeFile(
    path.join(skillDir, ".magi-skill-manifest.json"),
    JSON.stringify({ version: 1, files }, null, 2),
    "utf8",
  );
}

function promptSkillYaml(lines: string[]): string {
  return [
    "---",
    'name: hook-skill',
    'description: "Checks runtime hook behavior."',
    ...lines,
    "---",
    "body",
  ].join("\n");
}

function hookCtx(turnId = "turn-1"): HookContext {
  return {
    botId: "bot-hooks",
    userId: "user-hooks",
    sessionKey: "agent:main:test:hooks",
    turnId,
    llm: {} as LLMClient,
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "claude-opus-4-7",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
}

describe("SkillLoader trusted runtime hooks", () => {
  let workspaceRoot: string;
  let workspaceSkills: string;
  let operatorRoot: string;
  let operatorSkills: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "skill-hooks-ws-"));
    workspaceSkills = path.join(workspaceRoot, "skills");
    await fs.mkdir(workspaceSkills, { recursive: true });
    operatorRoot = await fs.mkdtemp(path.join(os.tmpdir(), "skill-hooks-op-"));
    operatorSkills = path.join(operatorRoot, "skills");
    await fs.mkdir(operatorSkills, { recursive: true });
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
    await fs.rm(operatorRoot, { recursive: true, force: true });
  });

  it("rejects mutable workspace hooks.command while still loading prompt skill", async () => {
    const skillDir = await writeSkill(
      workspaceSkills,
      "hook-skill",
      promptSkillYaml([
        "hooks:",
        "  PreToolUse:",
        "    - matcher: Bash",
        "      command: ./hooks/check.sh",
      ]),
    );
    await writeHook(skillDir, "hooks/check.sh");

    const { tools, report } = await loadSkillsFromDir({
      skillsDir: workspaceSkills,
      workspaceRoot,
    });

    expect(tools).toHaveLength(1);
    expect(report.runtimeHooks).toEqual([]);
    expect(report.loaded[0]?.runtimeHooks).toBe(0);
    expect(report.issues[0]).toMatchObject({
      reason: "runtime_hook_invalid",
      skillName: "hook-skill",
    });
    expect(report.issues[0]?.detail).toContain("untrusted");
  });

  it("registers and executes command hooks from a trusted operator root", async () => {
    const skillDir = await writeSkill(
      operatorSkills,
      "hook-skill",
      promptSkillYaml([
        "hooks:",
        "  PreToolUse:",
        "    - matcher: Bash",
        "      command: ./hooks/check.sh",
        "      timeout: 5000",
        "      statusMessage: Checking shell command",
      ]),
    );
    await writeHook(
      skillDir,
      "hooks/check.sh",
      [
        "#!/bin/sh",
        'printf "%s|%s|%s|%s" "$MAGI_SKILL_ROOT" "$MAGI_HOOK_POINT" "$MAGI_TOOL_NAME" "$MAGI_TURN_ID" > "$MAGI_SKILL_ROOT/env.out"',
        "exit 0",
        "",
      ].join("\n"),
    );

    const { report } = await loadSkillsFromDir({
      skillsDir: operatorSkills,
      workspaceRoot,
      trustedSkillRoots: [operatorRoot],
    });
    expect(report.issues).toEqual([]);
    expect(report.runtimeHooks).toHaveLength(1);
    expect(report.runtimeHooks[0]).toMatchObject({
      skillName: "hook-skill",
      point: "beforeToolUse",
      if: "Bash(*)",
      action: "command",
      trustSource: "trusted_root",
    });

    const registry = new HookRegistry();
    expect(registerSkillRuntimeHooks(registry, report.runtimeHooks)).toBe(1);
    const outcome = await registry.runPre(
      "beforeToolUse",
      { toolName: "Bash", toolUseId: "tu-1", input: { command: "pwd" } },
      hookCtx("turn-env"),
    );

    expect(outcome.action).toBe("continue");
    const envOut = await fs.readFile(path.join(skillDir, "env.out"), "utf8");
    expect(envOut).toBe(`${await fs.realpath(skillDir)}|beforeToolUse|Bash|turn-env`);
  });

  it("reports invalid hooks schema and registers zero hooks", async () => {
    await writeSkill(
      workspaceSkills,
      "hook-skill",
      promptSkillYaml([
        "hooks:",
        "  PreToolUse:",
        "    matcher: Bash",
        "    command: ./hooks/check.sh",
      ]),
    );

    const { report } = await loadSkillsFromDir({
      skillsDir: workspaceSkills,
      workspaceRoot,
    });

    expect(report.runtimeHooks).toEqual([]);
    expect(report.issues[0]).toMatchObject({ reason: "runtime_hook_invalid" });
    expect(report.issues[0]?.detail).toContain("must be an array");
  });

  it("allows static non-executable runtime_hooks metadata from mutable skills", async () => {
    await writeSkill(
      workspaceSkills,
      "hook-skill",
      [
        "---",
        'name: hook-skill',
        'description: "Declares policy metadata."',
        "runtime_hooks:",
        "  - name: ask-before-bash",
        "    point: beforeToolUse",
        '    if: "Bash(*)"',
        "    decision: ask",
        '    reason: "static policy metadata"',
        "---",
        "body",
      ].join("\n"),
    );

    const { report } = await loadSkillsFromDir({
      skillsDir: workspaceSkills,
      workspaceRoot,
    });

    expect(report.issues).toEqual([]);
    expect(report.runtimeHooks).toHaveLength(1);
    expect(report.runtimeHooks[0]).toMatchObject({
      action: "permission_decision",
      decision: "ask",
    });
  });

  it("rejects trusted hook script symlinks that escape the skill root", async () => {
    const skillDir = await writeSkill(
      operatorSkills,
      "hook-skill",
      promptSkillYaml([
        "hooks:",
        "  PreToolUse:",
        "    - matcher: Bash",
        "      command: ./hooks/check.sh",
      ]),
    );
    const outside = path.join(operatorRoot, "outside.sh");
    await fs.writeFile(outside, "#!/bin/sh\nexit 0\n", { encoding: "utf8", mode: 0o755 });
    await fs.mkdir(path.join(skillDir, "hooks"), { recursive: true });
    await fs.symlink(outside, path.join(skillDir, "hooks/check.sh"));

    const { report } = await loadSkillsFromDir({
      skillsDir: operatorSkills,
      workspaceRoot,
      trustedSkillRoots: [operatorRoot],
    });

    expect(report.runtimeHooks).toEqual([]);
    expect(report.issues[0]?.detail).toContain("escapes skill root");
  });

  it("rejects parent-directory hook commands before realpath resolution", async () => {
    await writeSkill(
      operatorSkills,
      "hook-skill",
      promptSkillYaml([
        "hooks:",
        "  PreToolUse:",
        "    - matcher: Bash",
        "      command: ../payload.sh",
      ]),
    );
    await fs.writeFile(path.join(operatorSkills, "payload.sh"), "#!/bin/sh\nexit 0\n");

    const { report } = await loadSkillsFromDir({
      skillsDir: operatorSkills,
      workspaceRoot,
      trustedSkillRoots: [operatorRoot],
    });

    expect(report.runtimeHooks).toEqual([]);
    expect(report.issues[0]?.detail).toContain("parent traversal");
  });

  it("rejects manifest-trusted command hooks when a covered digest mismatches", async () => {
    const skillDir = await writeSkill(
      workspaceSkills,
      "hook-skill",
      promptSkillYaml([
        "hooks:",
        "  PreToolUse:",
        "    - matcher: Bash",
        "      command: ./hooks/check.sh",
      ]),
    );
    const script = await writeHook(skillDir, "hooks/check.sh");
    await writeManifest(skillDir, {
      "SKILL.md": `sha256:${"0".repeat(64)}`,
      "hooks/check.sh": `sha256:${await sha256File(script)}`,
    });

    const { report } = await loadSkillsFromDir({
      skillsDir: workspaceSkills,
      workspaceRoot,
    });

    expect(report.runtimeHooks).toEqual([]);
    expect(report.issues[0]?.detail).toContain("digest mismatch");
  });

  it("registers command hooks from a verified digest manifest", async () => {
    const skillDir = await writeSkill(
      workspaceSkills,
      "hook-skill",
      promptSkillYaml([
        "hooks:",
        "  Stop:",
        "    - matcher: beforeCommit",
        "      command: ./hooks/stop.sh",
      ]),
    );
    const script = await writeHook(skillDir, "hooks/stop.sh");
    await writeManifest(skillDir, {
      "SKILL.md": `sha256:${await sha256File(path.join(skillDir, "SKILL.md"))}`,
      "hooks/stop.sh": `sha256:${await sha256File(script)}`,
    });

    const { report } = await loadSkillsFromDir({
      skillsDir: workspaceSkills,
      workspaceRoot,
    });

    expect(report.issues).toEqual([]);
    expect(report.runtimeHooks).toHaveLength(1);
    expect(report.runtimeHooks[0]).toMatchObject({
      point: "beforeCommit",
      if: "beforeCommit",
      trustSource: "manifest",
    });
  });

  it("does not leak runtime_hooks from a skill that fails later validation", async () => {
    await writeSkill(
      workspaceSkills,
      "hook-skill",
      [
        "---",
        'name: hook-skill',
        'description: "Fails after declaring runtime hooks."',
        "kind: skill",
        "entry: ./run.sh",
        "runtime_hooks:",
        "  - name: ask-before-bash",
        "    point: beforeToolUse",
        '    if: "Bash(*)"',
        "    decision: ask",
        '    reason: "should not leak"',
        "---",
        "body",
      ].join("\n"),
    );

    const { tools, report } = await loadSkillsFromDir({
      skillsDir: workspaceSkills,
      workspaceRoot,
    });

    expect(tools).toEqual([]);
    expect(report.runtimeHooks).toEqual([]);
    expect(report.issues.some((issue) => issue.reason === "missing_input_schema")).toBe(true);
  });
});
