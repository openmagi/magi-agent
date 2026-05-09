/**
 * SpawnWorkspace — ephemeral `.spawn/{taskId}/` helpers.
 *
 * Extracted from tools/SpawnAgent.ts (R4 step 3, 2026-04-19). These are
 * filesystem-only utilities with no Agent/Tool coupling, so they live
 * alongside the spawn runtime rather than inside the Tool factory.
 *
 * Layout invariants (PRE-01, §7.12.d):
 *   • Subdir path: `{parentWorkspaceRoot}/.spawn/{taskId}/`
 *   • A `.gitignore` containing `*` is written once per parent root so
 *     git / hipocampus / health-monitor observers filter child scratch.
 *   • Workspace(spawnDir) enforces path-scope at tool-execution time.
 */

import fs from "node:fs/promises";
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { Workspace } from "../storage/Workspace.js";

const execFileAsync = promisify(execFile);

/** Generate a collision-resistant ephemeral spawn task id. */
export function randomTaskId(): string {
  const rand = Math.random().toString(36).slice(2, 10);
  return `spawn_${Date.now().toString(36)}_${rand}`;
}

/**
 * PRE-01 helper: prepare the ephemeral subdir for a spawned child.
 * Creates `{parentWorkspaceRoot}/.spawn/{taskId}/` (recursive) and
 * drops a best-effort `.gitignore` into `.spawn/`.
 */
export async function prepareSpawnDir(
  parentWorkspaceRoot: string,
  taskId: string,
): Promise<{ spawnDir: string; spawnWorkspace: Workspace }> {
  const spawnRoot = path.join(parentWorkspaceRoot, ".spawn");
  const spawnDir = path.join(spawnRoot, taskId);
  await fs.mkdir(spawnDir, { recursive: true });
  // Best-effort .gitignore — never blocks child execution.
  try {
    const ignorePath = path.join(spawnRoot, ".gitignore");
    await fs.writeFile(ignorePath, "*\n", { flag: "wx" });
  } catch {
    /* already exists — fine */
  }
  const spawnWorkspace = new Workspace(spawnDir);
  return { spawnDir, spawnWorkspace };
}

/**
 * Prepare `.spawn/{taskId}/worktree` as a detached git worktree rooted at
 * the parent's current HEAD. The outer spawnDir remains the audit/scratch
 * envelope; child file tools run inside the worktree.
 */
export async function prepareGitWorktreeSpawnDir(
  parentWorkspaceRoot: string,
  taskId: string,
): Promise<{ spawnDir: string; spawnWorkspace: Workspace; worktreeDir: string }> {
  const prepared = await prepareSpawnDir(parentWorkspaceRoot, taskId);
  const worktreeDir = path.join(prepared.spawnDir, "worktree");
  await execFileAsync("git", ["rev-parse", "--is-inside-work-tree"], {
    cwd: parentWorkspaceRoot,
  });
  await execFileAsync("git", ["worktree", "add", "--detach", worktreeDir, "HEAD"], {
    cwd: parentWorkspaceRoot,
    maxBuffer: 1024 * 1024,
  });
  return {
    spawnDir: prepared.spawnDir,
    spawnWorkspace: new Workspace(worktreeDir),
    worktreeDir,
  };
}

export async function canPrepareGitWorktreeSpawnDir(
  parentWorkspaceRoot: string,
): Promise<boolean> {
  try {
    await execFileAsync("git", ["rev-parse", "--is-inside-work-tree"], {
      cwd: parentWorkspaceRoot,
    });
    await execFileAsync("git", ["rev-parse", "--verify", "HEAD"], {
      cwd: parentWorkspaceRoot,
    });
    return true;
  } catch {
    return false;
  }
}

/** Recursively count regular files under `dir`. Returns 0 if absent. */
export async function countFilesRecursive(dir: string): Promise<number> {
  let count = 0;
  let entries: Array<{ name: string; isDir: boolean; isFile: boolean }>;
  try {
    const raw = await fs.readdir(dir, { withFileTypes: true });
    entries = raw.map((d) => ({
      name: d.name,
      isDir: d.isDirectory(),
      isFile: d.isFile(),
    }));
  } catch {
    return 0;
  }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isFile) count += 1;
    else if (e.isDir) count += await countFilesRecursive(full);
  }
  return count;
}
