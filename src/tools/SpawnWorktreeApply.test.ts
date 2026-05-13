import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { prepareGitWorktreeSpawnDir } from "../spawn/SpawnWorkspace.js";
import { makeSpawnWorktreeApplyTool, type SpawnWorktreeApplyOutput } from "./SpawnWorktreeApply.js";

const execFileAsync = promisify(execFile);

function makeCtx(root: string): ToolContext {
  return {
    botId: "bot-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    workspaceRoot: root,
    askUser: async () => ({ selectedId: "approve" }),
    emitProgress: () => {},
    abortSignal: new AbortController().signal,
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

async function initRepo(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "spawn-worktree-apply-"));
  await execFileAsync("git", ["init", "-q"], { cwd: root });
  await execFileAsync("git", ["config", "user.email", "bot@example.com"], { cwd: root });
  await execFileAsync("git", ["config", "user.name", "Bot"], { cwd: root });
  await fs.mkdir(path.join(root, "src"), { recursive: true });
  await fs.writeFile(path.join(root, "README.md"), "# repo\n", "utf8");
  await fs.writeFile(path.join(root, "src/existing.ts"), "export const value = 1;\n", "utf8");
  await execFileAsync("git", ["add", "."], { cwd: root });
  await execFileAsync("git", ["commit", "-m", "init", "-q"], { cwd: root });
  return root;
}

async function writeChildChanges(worktreeDir: string): Promise<void> {
  await fs.writeFile(path.join(worktreeDir, "src/existing.ts"), "export const value = 2;\n", "utf8");
  await fs.writeFile(path.join(worktreeDir, "src/new.ts"), "export const created = true;\n", "utf8");
  await fs.rm(path.join(worktreeDir, "README.md"));
}

describe("SpawnWorktreeApply", () => {
  it("previews changed, created, and deleted files from a child git worktree", async () => {
    const root = await initRepo();
    try {
      const prepared = await prepareGitWorktreeSpawnDir(root, "spawn_preview");
      await writeChildChanges(prepared.worktreeDir);

      const tool = makeSpawnWorktreeApplyTool(root);
      const result = await tool.execute(
        { action: "preview", spawnDir: ".spawn/spawn_preview" },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      const output = result.output as SpawnWorktreeApplyOutput;
      expect(output.action).toBe("preview");
      expect(output.changedFiles).toEqual(["README.md", "src/existing.ts", "src/new.ts"]);
      expect(output.deletedFiles).toEqual(["README.md"]);
      expect(output.createdFiles).toEqual(["src/new.ts"]);
      expect(output.modifiedFiles).toEqual(["src/existing.ts"]);
      expect(output.diff).toContain("-export const value = 1;");
      expect(output.diff).toContain("+export const value = 2;");
      expect(output.diff).toContain("+export const created = true;");
      await expect(fs.access(path.join(root, "src/new.ts"))).rejects.toBeDefined();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("applies child worktree changes to the parent checkout and can clean up the spawn dir", async () => {
    const root = await initRepo();
    try {
      const prepared = await prepareGitWorktreeSpawnDir(root, "spawn_apply");
      await writeChildChanges(prepared.worktreeDir);

      const tool = makeSpawnWorktreeApplyTool(root);
      const result = await tool.execute(
        { action: "apply", spawnDir: ".spawn/spawn_apply", cleanup: true },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      const output = result.output as SpawnWorktreeApplyOutput;
      expect(output.action).toBe("apply");
      expect(output.applied).toBe(true);
      expect(output.cleanedUp).toBe(true);
      await expect(fs.readFile(path.join(root, "src/existing.ts"), "utf8")).resolves.toBe(
        "export const value = 2;\n",
      );
      await expect(fs.readFile(path.join(root, "src/new.ts"), "utf8")).resolves.toBe(
        "export const created = true;\n",
      );
      await expect(fs.access(path.join(root, "README.md"))).rejects.toBeDefined();
      await expect(fs.access(prepared.spawnDir)).rejects.toBeDefined();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("can adopt a child worktree commit through cherry-pick and clean up the child", async () => {
    const root = await initRepo();
    try {
      const prepared = await prepareGitWorktreeSpawnDir(root, "spawn_cherry_pick");
      await writeChildChanges(prepared.worktreeDir);

      const tool = makeSpawnWorktreeApplyTool(root);
      const result = await tool.execute(
        { action: "cherry_pick", spawnDir: ".spawn/spawn_cherry_pick", cleanup: true },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      const output = result.output as SpawnWorktreeApplyOutput;
      expect(output.action).toBe("cherry_pick");
      expect(output.applied).toBe(true);
      expect(output.cleanedUp).toBe(true);
      expect(output.mergeStrategy).toBe("cherry_pick");
      expect(output.adoptedCommit).toMatch(/^[a-f0-9]{40}$/);
      await expect(fs.readFile(path.join(root, "src/existing.ts"), "utf8")).resolves.toBe(
        "export const value = 2;\n",
      );
      await expect(fs.readFile(path.join(root, "src/new.ts"), "utf8")).resolves.toBe(
        "export const created = true;\n",
      );
      await expect(fs.access(path.join(root, "README.md"))).rejects.toBeDefined();
      await expect(fs.access(prepared.spawnDir)).rejects.toBeDefined();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("returns structured output when cherry-pick adoption conflicts", async () => {
    const root = await initRepo();
    try {
      const prepared = await prepareGitWorktreeSpawnDir(root, "spawn_cherry_conflict");
      await fs.writeFile(
        path.join(prepared.worktreeDir, "src/existing.ts"),
        "export const value = 2;\n",
        "utf8",
      );
      await fs.writeFile(path.join(root, "src/existing.ts"), "export const value = 99;\n", "utf8");
      await execFileAsync("git", ["add", "src/existing.ts"], { cwd: root });
      await execFileAsync("git", ["commit", "-m", "parent update", "-q"], { cwd: root });

      const tool = makeSpawnWorktreeApplyTool(root);
      const result = await tool.execute(
        { action: "cherry_pick", spawnDir: ".spawn/spawn_cherry_conflict", cleanup: true },
        makeCtx(root),
      );

      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("cherry_pick_conflict");
      expect(result.output).toMatchObject({
        action: "cherry_pick",
        applied: false,
        cleanedUp: false,
        mergeStrategy: "cherry_pick",
        conflictedFiles: ["src/existing.ts"],
      });
      expect((result.output as SpawnWorktreeApplyOutput | undefined)?.adoptedCommit).toMatch(
        /^[a-f0-9]{40}$/,
      );
      await expect(fs.access(prepared.spawnDir)).resolves.toBeUndefined();
      await expect(fs.readFile(path.join(root, "src/existing.ts"), "utf8")).resolves.toBe(
        "export const value = 99;\n",
      );
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("refuses to apply when the parent has dirty changes to the same files", async () => {
    const root = await initRepo();
    try {
      const prepared = await prepareGitWorktreeSpawnDir(root, "spawn_conflict");
      await writeChildChanges(prepared.worktreeDir);
      await fs.writeFile(path.join(root, "src/existing.ts"), "export const value = 99;\n", "utf8");

      const tool = makeSpawnWorktreeApplyTool(root);
      const result = await tool.execute(
        { action: "apply", spawnDir: ".spawn/spawn_conflict" },
        makeCtx(root),
      );

      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("parent_dirty_conflict");
      expect(result.errorMessage).toContain("src/existing.ts");
      expect(result.output).toMatchObject({
        action: "apply",
        applied: false,
        cleanedUp: false,
        mergeStrategy: "copy",
        conflictedFiles: ["src/existing.ts"],
        changedFiles: ["README.md", "src/existing.ts", "src/new.ts"],
      });
      await expect(fs.readFile(path.join(root, "src/existing.ts"), "utf8")).resolves.toBe(
        "export const value = 99;\n",
      );
      await expect(fs.access(prepared.spawnDir)).resolves.toBeUndefined();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("rejects child worktree changes by removing the child worktree without touching the parent", async () => {
    const root = await initRepo();
    try {
      const prepared = await prepareGitWorktreeSpawnDir(root, "spawn_reject");
      await writeChildChanges(prepared.worktreeDir);

      const tool = makeSpawnWorktreeApplyTool(root);
      const result = await tool.execute(
        { action: "reject", spawnDir: ".spawn/spawn_reject" },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      const output = result.output as SpawnWorktreeApplyOutput;
      expect(output.action).toBe("reject");
      expect(output.applied).toBe(false);
      expect(output.cleanedUp).toBe(true);
      await expect(fs.readFile(path.join(root, "src/existing.ts"), "utf8")).resolves.toBe(
        "export const value = 1;\n",
      );
      await expect(fs.readFile(path.join(root, "README.md"), "utf8")).resolves.toBe("# repo\n");
      await expect(fs.access(path.join(root, "src/new.ts"))).rejects.toBeDefined();
      await expect(fs.access(prepared.spawnDir)).rejects.toBeDefined();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("reports a no-op copy apply as unapplied while still honoring cleanup", async () => {
    const root = await initRepo();
    try {
      const prepared = await prepareGitWorktreeSpawnDir(root, "spawn_noop_apply");

      const tool = makeSpawnWorktreeApplyTool(root);
      const result = await tool.execute(
        { action: "apply", spawnDir: ".spawn/spawn_noop_apply", cleanup: true },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      expect(result.output).toMatchObject({
        action: "apply",
        applied: false,
        cleanedUp: true,
        mergeStrategy: "copy",
        changedFiles: [],
      });
      await expect(fs.readFile(path.join(root, "src/existing.ts"), "utf8")).resolves.toBe(
        "export const value = 1;\n",
      );
      await expect(fs.access(prepared.spawnDir)).rejects.toBeDefined();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("honors cleanup for no-op cherry-pick adoption", async () => {
    const root = await initRepo();
    try {
      const prepared = await prepareGitWorktreeSpawnDir(root, "spawn_noop_cherry_pick");

      const tool = makeSpawnWorktreeApplyTool(root);
      const result = await tool.execute(
        { action: "cherry_pick", spawnDir: ".spawn/spawn_noop_cherry_pick", cleanup: true },
        makeCtx(root),
      );

      expect(result.status).toBe("ok");
      expect(result.output).toMatchObject({
        action: "cherry_pick",
        applied: false,
        cleanedUp: true,
        mergeStrategy: "cherry_pick",
        changedFiles: [],
      });
      await expect(fs.readFile(path.join(root, "src/existing.ts"), "utf8")).resolves.toBe(
        "export const value = 1;\n",
      );
      await expect(fs.access(prepared.spawnDir)).rejects.toBeDefined();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });
});
