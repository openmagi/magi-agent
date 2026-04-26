/**
 * SkillLoader tests — Phase 2a contract + Phase 2b prompt-only path.
 *
 * Covers:
 *  (a) prompt-only skill loads when frontmatter has neither input_schema
 *      nor entry (inferred prompt-only).
 *  (b) explicit `kind: prompt` loads even if one of input_schema/entry
 *      is present (explicit wins).
 *  (c) prompt-only tool invocation returns the SKILL.md body as
 *      tool_result content.
 *  (d) prompt-only body larger than PROMPT_BODY_MAX_BYTES is truncated
 *      and `truncated: true` surfaces through metadata.
 *  (e) filterToolsByIntent routes prompt-only skills by tags just like
 *      regular script skills.
 *  (f) mixed set (prompt-only + script-backed) both register and coexist.
 *  (g) Phase 2a contract enforced — `kind: skill` missing input_schema is
 *      rejected with reason missing_input_schema.
 *  (h) Phase 2a contract enforced — `kind: skill` missing entry is
 *      rejected with reason entry_not_found.
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, it, expect } from "vitest";
import type { ToolContext } from "../Tool.js";
import {
  loadSkillsFromDir,
  PROMPT_BODY_MAX_BYTES,
} from "./SkillLoader.js";
import { filterToolsByIntent } from "../rules/IntentClassifier.js";

function makeCtx(workspaceRoot: string): ToolContext {
  return {
    botId: "bot_sl",
    sessionKey: "agent:main:sl:1",
    turnId: "turn_sl",
    workspaceRoot,
    abortSignal: new AbortController().signal,
    emitProgress: () => {},
    emitAgentEvent: () => {},
    askUser: async () => {
      throw new Error("no askUser in SkillLoader test");
    },
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

async function writeSkill(
  skillsDir: string,
  name: string,
  content: string,
): Promise<void> {
  const dir = path.join(skillsDir, name);
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(path.join(dir, "SKILL.md"), content, "utf8");
}

describe("SkillLoader — Phase 2b prompt-only path", () => {
  let workspaceRoot: string;
  let skillsDir: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "sl-ws-"));
    skillsDir = path.join(workspaceRoot, "skills");
    await fs.mkdir(skillsDir, { recursive: true });
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("(a) loads a prompt-only skill inferred from missing input_schema/entry", async () => {
    await writeSkill(
      skillsDir,
      "brainstorming",
      [
        "---",
        "name: brainstorming",
        'description: "Explores user intent before implementation."',
        "tags: [design, planning]",
        "---",
        "",
        "# Brainstorming",
        "Body content here.",
      ].join("\n"),
    );

    const { tools, report } = await loadSkillsFromDir({
      skillsDir,
      workspaceRoot,
    });

    expect(report.issues).toEqual([]);
    expect(tools).toHaveLength(1);
    const tool = tools[0]!;
    expect(tool.name).toBe("brainstorming");
    expect(tool.kind).toBe("skill");
    expect(tool.tags).toEqual(["design", "planning"]);
    expect(report.loaded[0]).toMatchObject({
      name: "brainstorming",
      scriptBacked: false,
      promptOnly: true,
    });
  });

  it("(b) explicit `kind: prompt` wins even when input_schema is present", async () => {
    await writeSkill(
      skillsDir,
      "mixed-kind",
      [
        "---",
        "name: mixed-kind",
        'description: "Returns an explicit prompt context."',
        "kind: prompt",
        'input_schema: { "type": "object" }',
        "---",
        "",
        "explicit prompt body",
      ].join("\n"),
    );

    const { tools, report } = await loadSkillsFromDir({
      skillsDir,
      workspaceRoot,
    });

    expect(report.issues).toEqual([]);
    expect(tools).toHaveLength(1);
    expect(report.loaded[0]?.promptOnly).toBe(true);
  });

  it("(c) prompt-only tool execute() returns SKILL.md body as content", async () => {
    const body = "# Fetch Full\n\nRetrieves original full content.";
    await writeSkill(
      skillsDir,
      "fetch-full",
      [
        "---",
        "name: fetch-full",
        'description: "Retrieves the original full content."',
        "---",
        "",
        body,
      ].join("\n"),
    );

    const { tools } = await loadSkillsFromDir({ skillsDir, workspaceRoot });
    expect(tools).toHaveLength(1);

    const res = await tools[0]!.execute({}, makeCtx(workspaceRoot));
    expect(res.status).toBe("ok");
    const output = res.output as { content: string; truncated: boolean };
    expect(output.content).toContain("Fetch Full");
    expect(output.content).toContain("Retrieves original full content.");
    expect(output.truncated).toBe(false);
    expect(res.metadata?.promptOnly).toBe(true);
  });

  it("(d) prompt-only body exceeding PROMPT_BODY_MAX_BYTES is truncated", async () => {
    const huge = "a".repeat(PROMPT_BODY_MAX_BYTES + 500);
    await writeSkill(
      skillsDir,
      "huge-skill",
      [
        "---",
        "name: huge-skill",
        'description: "Enormous prompt body for truncation test."',
        "---",
        "",
        huge,
      ].join("\n"),
    );

    const { tools } = await loadSkillsFromDir({ skillsDir, workspaceRoot });
    const res = await tools[0]!.execute({}, makeCtx(workspaceRoot));
    const output = res.output as { content: string; truncated: boolean };
    expect(output.truncated).toBe(true);
    expect(output.content).toContain("[...TRUNCATED");
    // Body slice itself must be <= MAX; total string includes marker.
    expect(Buffer.byteLength(output.content, "utf8")).toBeLessThanOrEqual(
      PROMPT_BODY_MAX_BYTES + 64,
    );
  });

  it("(e) filterToolsByIntent routes prompt-only skills by tags", async () => {
    await writeSkill(
      skillsDir,
      "legal-helper",
      [
        "---",
        "name: legal-helper",
        'description: "Drafts legal reasoning summaries."',
        "tags: [legal]",
        "---",
        "legal body",
      ].join("\n"),
    );
    await writeSkill(
      skillsDir,
      "design-helper",
      [
        "---",
        "name: design-helper",
        'description: "Explores design options."',
        "tags: [design]",
        "---",
        "design body",
      ].join("\n"),
    );

    const { tools } = await loadSkillsFromDir({ skillsDir, workspaceRoot });
    expect(tools).toHaveLength(2);

    const filtered = filterToolsByIntent(tools, ["legal"]);
    expect(filtered.map((t) => t.name)).toEqual(["legal-helper"]);

    const generalAll = filterToolsByIntent(tools, ["general"]);
    expect(generalAll.map((t) => t.name).sort()).toEqual([
      "design-helper",
      "legal-helper",
    ]);
  });

  it("(f) mixed set: prompt-only + script-backed skills coexist", async () => {
    // Prompt-only.
    await writeSkill(
      skillsDir,
      "prompt-one",
      [
        "---",
        "name: prompt-one",
        'description: "Explains something via prompt."',
        "tags: [guide]",
        "---",
        "body",
      ].join("\n"),
    );
    // Script-backed — needs entry file + input_schema.
    const scriptDir = path.join(skillsDir, "script-one");
    await fs.mkdir(scriptDir, { recursive: true });
    await fs.writeFile(
      path.join(scriptDir, "SKILL.md"),
      [
        "---",
        "name: script-one",
        'description: "Runs a deterministic script."',
        "entry: run.sh",
        'input_schema: { "type": "object" }',
        "tags: [utility]",
        "---",
        "reference body (not injected)",
      ].join("\n"),
      "utf8",
    );
    await fs.writeFile(
      path.join(scriptDir, "run.sh"),
      '#!/bin/sh\necho \'{"ok": true}\'\n',
      "utf8",
    );

    const { tools, report } = await loadSkillsFromDir({
      skillsDir,
      workspaceRoot,
    });

    expect(report.issues).toEqual([]);
    expect(tools.map((t) => t.name).sort()).toEqual([
      "prompt-one",
      "script-one",
    ]);
    const loadedByName = Object.fromEntries(
      report.loaded.map((l) => [l.name, l]),
    );
    expect(loadedByName["prompt-one"]?.scriptBacked).toBe(false);
    expect(loadedByName["prompt-one"]?.promptOnly).toBe(true);
    expect(loadedByName["script-one"]?.scriptBacked).toBe(true);
  });

  it("(g) Phase 2a contract — `kind: skill` missing input_schema is rejected", async () => {
    await writeSkill(
      skillsDir,
      "broken-skill",
      [
        "---",
        "name: broken-skill",
        'description: "Does a thing but is mis-declared."',
        "kind: skill",
        "entry: run.sh",
        "---",
        "body",
      ].join("\n"),
    );

    const { tools, report } = await loadSkillsFromDir({
      skillsDir,
      workspaceRoot,
    });

    expect(tools).toEqual([]);
    expect(report.issues).toHaveLength(1);
    expect(report.issues[0]?.reason).toBe("missing_input_schema");
  });

  it("(h) Phase 2a contract — `kind: skill` missing entry is rejected", async () => {
    await writeSkill(
      skillsDir,
      "no-entry-skill",
      [
        "---",
        "name: no-entry-skill",
        'description: "Declares input_schema but no entry."',
        "kind: skill",
        'input_schema: { "type": "object" }',
        "---",
        "body",
      ].join("\n"),
    );

    const { tools, report } = await loadSkillsFromDir({
      skillsDir,
      workspaceRoot,
    });

    expect(tools).toEqual([]);
    expect(report.issues).toHaveLength(1);
    expect(report.issues[0]?.reason).toBe("entry_not_found");
  });

  it("(i) loads POS/Tossplace product templates with routing tags", async () => {
    const repoRoot = path.resolve(
      path.dirname(fileURLToPath(import.meta.url)),
      "../../../../..",
    );
    const templatesDir = path.join(repoRoot, "src/lib/templates/skills");
    const skillNames = [
      "pos-sales",
      "pos-menu-strategy",
      "pos-accounting",
      "pos-inventory",
      "tossplace-pos",
    ];
    for (const skillName of skillNames) {
      await fs.cp(
        path.join(templatesDir, skillName),
        path.join(skillsDir, skillName),
        { recursive: true },
      );
    }

    const { tools, report } = await loadSkillsFromDir({
      skillsDir,
      workspaceRoot,
    });

    expect(report.issues).toEqual([]);
    expect(tools.map((t) => t.name).sort()).toEqual(skillNames.sort());
    for (const tool of tools) {
      expect(tool.tags).toContain("pos");
      expect(tool.tags).toContain("tossplace");
    }
    expect(tools.find((t) => t.name === "pos-sales")?.tags).toEqual(
      expect.arrayContaining(["sales", "store"]),
    );
  });

  it("(j) keeps tagged skills inside the general fallback cap", () => {
    const coreTool = { name: "FileRead", kind: "core" as const };
    const untagged = Array.from({ length: 10 }, (_, i) => ({
      name: `a-untagged-${String(i).padStart(2, "0")}`,
      kind: "skill" as const,
      tags: [],
    }));
    const posTool = {
      name: "pos-sales",
      kind: "skill" as const,
      tags: ["pos", "tossplace", "sales"],
    };

    const filtered = filterToolsByIntent(
      [coreTool, ...untagged, posTool],
      ["general"],
      4,
    );

    expect(filtered.map((t) => t.name)).toContain("pos-sales");
  });
});
