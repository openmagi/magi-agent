import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { errorResult } from "../util/toolResult.js";

const execFileAsync = promisify(execFile);

export type SpawnWorktreeApplyAction = "preview" | "apply" | "reject" | "cherry_pick";

export interface SpawnWorktreeApplyInput {
  action: SpawnWorktreeApplyAction;
  /** `.spawn/<taskId>` path returned by SpawnAgent artifacts.spawnDir. */
  spawnDir: string;
  /** Remove `.spawn/<taskId>` after a successful apply. Reject always cleans up. */
  cleanup?: boolean;
  /** Maximum diff bytes returned for preview/apply summaries. */
  maxBytes?: number;
}

export interface SpawnWorktreeApplyOutput {
  action: SpawnWorktreeApplyAction;
  spawnDir: string;
  worktreeDir: string;
  changedFiles: string[];
  createdFiles: string[];
  modifiedFiles: string[];
  deletedFiles: string[];
  diff: string;
  truncated: boolean;
  applied: boolean;
  cleanedUp: boolean;
  mergeStrategy?: "copy" | "cherry_pick";
  adoptedCommit?: string;
  conflictedFiles?: string[];
}

interface WorktreeChanges {
  changedFiles: string[];
  createdFiles: string[];
  modifiedFiles: string[];
  deletedFiles: string[];
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: {
      type: "string",
      enum: ["preview", "apply", "reject", "cherry_pick"],
      description:
        "preview lists child worktree changes, apply copies them into the parent checkout, cherry_pick adopts a child commit into the parent checkout, reject discards the child worktree.",
    },
    spawnDir: {
      type: "string",
      description: "Workspace-relative or absolute .spawn/<taskId> directory returned by SpawnAgent.",
    },
    cleanup: {
      type: "boolean",
      description: "When action='apply', remove the child worktree and spawnDir after a successful apply.",
    },
    maxBytes: {
      type: "integer",
      minimum: 1000,
      description: "Maximum diff bytes returned. Default 256KB, max 1MB.",
    },
  },
  required: ["action", "spawnDir"],
} as const;

const DEFAULT_MAX_BYTES = 256 * 1024;
const MAX_BYTES = 1024 * 1024;

async function git(cwd: string, args: string[]): Promise<string> {
  const { stdout } = await execFileAsync("git", args, {
    cwd,
    maxBuffer: 4 * 1024 * 1024,
  });
  return stdout;
}

function truncate(text: string, maxBytes: number): { text: string; truncated: boolean } {
  const buf = Buffer.from(text, "utf8");
  if (buf.byteLength <= maxBytes) return { text, truncated: false };
  return {
    text: buf.subarray(0, maxBytes).toString("utf8"),
    truncated: true,
  };
}

function normalizeRelPath(value: string): string | null {
  const normalized = path.posix.normalize(value.replace(/\\/g, "/").replace(/^\/+/, ""));
  if (!normalized || normalized === "." || normalized === ".." || normalized.startsWith("../")) {
    return null;
  }
  return normalized;
}

function resolveSpawnDir(workspaceRoot: string, input: string): string {
  const root = path.resolve(workspaceRoot);
  const spawnRoot = path.join(root, ".spawn");
  const resolved = path.resolve(path.isAbsolute(input) ? input : path.join(root, input));
  if (resolved !== spawnRoot && !resolved.startsWith(`${spawnRoot}${path.sep}`)) {
    throw new Error(`spawnDir must be inside workspace .spawn/: ${input}`);
  }
  return resolved;
}

function parsePorcelainZ(status: string): WorktreeChanges {
  const created = new Set<string>();
  const modified = new Set<string>();
  const deleted = new Set<string>();
  const tokens = status.split("\0").filter((token) => token.length > 0);

  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i]!;
    if (token.length < 4) continue;
    const code = token.slice(0, 2);
    const rawPath = normalizeRelPath(token.slice(3));
    if (!rawPath) continue;
    if (code[0] === "R" || code[0] === "C") {
      i += 1;
    }
    if (code.includes("?") || code.includes("A")) {
      created.add(rawPath);
      continue;
    }
    if (code.includes("D")) {
      deleted.add(rawPath);
      continue;
    }
    modified.add(rawPath);
  }

  const changedFiles = new Set<string>([
    ...created,
    ...modified,
    ...deleted,
  ]);
  return {
    changedFiles: [...changedFiles].sort(),
    createdFiles: [...created].sort(),
    modifiedFiles: [...modified].sort(),
    deletedFiles: [...deleted].sort(),
  };
}

async function readChanges(worktreeDir: string): Promise<WorktreeChanges> {
  const status = await git(worktreeDir, [
    "status",
    "--porcelain=v1",
    "-z",
    "--untracked-files=all",
  ]);
  return parsePorcelainZ(status);
}

async function readCreatedFileDiff(worktreeDir: string, relPath: string): Promise<string> {
  try {
    const { stdout } = await execFileAsync(
      "git",
      ["diff", "--no-index", "--no-color", "--", "/dev/null", relPath],
      {
        cwd: worktreeDir,
        maxBuffer: 2 * 1024 * 1024,
      },
    );
    return stdout;
  } catch (err) {
    const stdout = (err as { stdout?: unknown }).stdout;
    return typeof stdout === "string" ? stdout : "";
  }
}

async function readReviewDiff(
  worktreeDir: string,
  changes: WorktreeChanges,
  maxBytes: number,
): Promise<{ text: string; truncated: boolean }> {
  const tracked = await git(worktreeDir, ["diff", "--no-ext-diff", "--no-color", "HEAD", "--"]);
  const createdDiffs = await Promise.all(
    changes.createdFiles.map((relPath) => readCreatedFileDiff(worktreeDir, relPath)),
  );
  return truncate([tracked, ...createdDiffs].filter(Boolean).join("\n"), maxBytes);
}

async function assertGitWorktree(worktreeDir: string): Promise<void> {
  const stat = await fs.stat(worktreeDir);
  if (!stat.isDirectory()) {
    throw new Error(`child worktree is not a directory: ${worktreeDir}`);
  }
  const inside = (await git(worktreeDir, ["rev-parse", "--is-inside-work-tree"])).trim();
  if (inside !== "true") {
    throw new Error(`child worktree is not a git worktree: ${worktreeDir}`);
  }
}

function intersect(a: readonly string[], b: readonly string[]): string[] {
  const right = new Set(b);
  return a.filter((item) => right.has(item)).sort();
}

async function parentDirtyConflictsForFiles(
  parentRoot: string,
  files: readonly string[],
): Promise<string[]> {
  if (files.length === 0) return [];
  const status = await git(parentRoot, [
    "status",
    "--porcelain=v1",
    "-z",
    "--untracked-files=all",
    "--",
    ...files,
  ]);
  const parentChanges = parsePorcelainZ(status).changedFiles;
  return intersect(files, parentChanges);
}

async function copyChildFile(parentRoot: string, worktreeDir: string, relPath: string): Promise<void> {
  const normalized = normalizeRelPath(relPath);
  if (!normalized) {
    throw new Error(`invalid child path: ${relPath}`);
  }
  const source = path.join(worktreeDir, normalized);
  const target = path.join(parentRoot, normalized);
  const sourceStat = await fs.lstat(source);
  if (!sourceStat.isFile()) {
    throw new Error(`child changed path is not a regular file: ${normalized}`);
  }
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.copyFile(source, target);
}

async function removeParentFile(parentRoot: string, relPath: string): Promise<void> {
  const normalized = normalizeRelPath(relPath);
  if (!normalized) {
    throw new Error(`invalid child path: ${relPath}`);
  }
  await fs.rm(path.join(parentRoot, normalized), { force: true });
}

async function cleanupSpawnWorktree(parentRoot: string, spawnDir: string, worktreeDir: string): Promise<void> {
  await execFileAsync("git", ["worktree", "remove", "--force", worktreeDir], {
    cwd: parentRoot,
    maxBuffer: 1024 * 1024,
  }).catch(async () => {
    await fs.rm(worktreeDir, { recursive: true, force: true });
  });
  await fs.rm(spawnDir, { recursive: true, force: true });
}

async function commitChildChanges(worktreeDir: string): Promise<string> {
  await git(worktreeDir, ["add", "-A"]);
  const staged = await git(worktreeDir, ["diff", "--cached", "--name-only"]);
  if (!staged.trim()) {
    return (await git(worktreeDir, ["rev-parse", "HEAD"])).trim();
  }
  await execFileAsync(
    "git",
    [
      "-c",
      "user.email=magi-bot@example.invalid",
      "-c",
      "user.name=Magi Bot",
      "commit",
      "-m",
      "Adopt child worktree changes",
    ],
    {
      cwd: worktreeDir,
      maxBuffer: 2 * 1024 * 1024,
    },
  );
  return (await git(worktreeDir, ["rev-parse", "HEAD"])).trim();
}

async function conflictedFiles(parentRoot: string): Promise<string[]> {
  const output = await git(parentRoot, ["diff", "--name-only", "--diff-filter=U"]);
  return output
    .split(/\r?\n/)
    .map((line) => normalizeRelPath(line))
    .filter((line): line is string => !!line)
    .sort();
}

async function abortCherryPick(parentRoot: string): Promise<void> {
  await execFileAsync("git", ["cherry-pick", "--abort"], {
    cwd: parentRoot,
    maxBuffer: 1024 * 1024,
  }).catch(async () => {
    await execFileAsync("git", ["reset", "--merge"], {
      cwd: parentRoot,
      maxBuffer: 1024 * 1024,
    }).catch(() => {});
  });
}

function outputFor(input: {
  action: SpawnWorktreeApplyAction;
  spawnDir: string;
  worktreeDir: string;
  changes: WorktreeChanges;
  diff: string;
  truncated: boolean;
  applied: boolean;
  cleanedUp: boolean;
  mergeStrategy?: "copy" | "cherry_pick";
  adoptedCommit?: string;
  conflictedFiles?: string[];
}): SpawnWorktreeApplyOutput {
  return {
    action: input.action,
    spawnDir: input.spawnDir,
    worktreeDir: input.worktreeDir,
    changedFiles: input.changes.changedFiles,
    createdFiles: input.changes.createdFiles,
    modifiedFiles: input.changes.modifiedFiles,
    deletedFiles: input.changes.deletedFiles,
    diff: input.diff,
    truncated: input.truncated,
    applied: input.applied,
    cleanedUp: input.cleanedUp,
    ...(input.mergeStrategy ? { mergeStrategy: input.mergeStrategy } : {}),
    ...(input.adoptedCommit ? { adoptedCommit: input.adoptedCommit } : {}),
    ...(input.conflictedFiles ? { conflictedFiles: input.conflictedFiles } : {}),
  };
}

export function makeSpawnWorktreeApplyTool(
  workspaceRoot: string,
): Tool<SpawnWorktreeApplyInput, SpawnWorktreeApplyOutput> {
  return {
    name: "SpawnWorktreeApply",
    description:
      "Preview, apply, cherry-pick, or reject changes produced by a SpawnAgent child running with workspace_policy='git_worktree'. Use preview first, then apply or cherry_pick only when the child changes should be adopted into the parent checkout. Apply refuses to overwrite dirty parent files.",
    inputSchema: INPUT_SCHEMA,
    permission: "write",
    mutatesWorkspace: true,
    isConcurrencySafe: false,
    validate(input) {
      if (!input || typeof input.spawnDir !== "string" || input.spawnDir.length === 0) {
        return "`spawnDir` is required";
      }
      if (
        input.action !== "preview" &&
        input.action !== "apply" &&
        input.action !== "reject" &&
        input.action !== "cherry_pick"
      ) {
        return "`action` must be 'preview', 'apply', 'cherry_pick', or 'reject'";
      }
      return null;
    },
    async execute(
      input: SpawnWorktreeApplyInput,
      ctx: ToolContext,
    ): Promise<ToolResult<SpawnWorktreeApplyOutput>> {
      const start = Date.now();
      try {
        const parentRoot = path.resolve(ctx.workspaceRoot || workspaceRoot);
        const spawnDir = resolveSpawnDir(parentRoot, input.spawnDir);
        const worktreeDir = path.join(spawnDir, "worktree");
        await assertGitWorktree(worktreeDir);

        const maxBytes = Math.min(MAX_BYTES, Math.max(1000, input.maxBytes ?? DEFAULT_MAX_BYTES));
        const changes = await readChanges(worktreeDir);
        const diff = await readReviewDiff(worktreeDir, changes, maxBytes);

        if (input.action === "preview") {
          return {
            status: "ok",
            output: outputFor({
              action: input.action,
              spawnDir,
              worktreeDir,
              changes,
              diff: diff.text,
              truncated: diff.truncated,
              applied: false,
              cleanedUp: false,
            }),
            durationMs: Date.now() - start,
            metadata: { changedFiles: changes.changedFiles },
          };
        }

        if (input.action === "reject") {
          await cleanupSpawnWorktree(parentRoot, spawnDir, worktreeDir);
          return {
            status: "ok",
            output: outputFor({
              action: input.action,
              spawnDir,
              worktreeDir,
              changes,
              diff: diff.text,
              truncated: diff.truncated,
              applied: false,
              cleanedUp: true,
            }),
            durationMs: Date.now() - start,
            metadata: { changedFiles: changes.changedFiles },
          };
        }

        const parentConflicts = await parentDirtyConflictsForFiles(parentRoot, changes.changedFiles);
        if (parentConflicts.length > 0) {
          const mergeStrategy = input.action === "cherry_pick" ? "cherry_pick" : "copy";
          return {
            status: "error",
            errorCode: "parent_dirty_conflict",
            errorMessage: `parent checkout has local changes for child worktree files: ${parentConflicts.join(", ")}`,
            output: outputFor({
              action: input.action,
              spawnDir,
              worktreeDir,
              changes,
              diff: diff.text,
              truncated: diff.truncated,
              applied: false,
              cleanedUp: false,
              mergeStrategy,
              conflictedFiles: parentConflicts,
            }),
            durationMs: Date.now() - start,
            metadata: {
              evidenceKind: "spawn_worktree_apply_conflict",
              conflictKind: "parent_dirty",
              conflictedFiles: parentConflicts,
              mergeStrategy,
            },
          };
        }

        if (input.action === "cherry_pick") {
          if (changes.changedFiles.length === 0) {
            const shouldCleanup = input.cleanup === true;
            if (shouldCleanup) {
              await cleanupSpawnWorktree(parentRoot, spawnDir, worktreeDir);
            }
            return {
              status: "ok",
              output: outputFor({
                action: input.action,
                spawnDir,
                worktreeDir,
                changes,
                diff: diff.text,
                truncated: diff.truncated,
                applied: false,
                cleanedUp: shouldCleanup,
                mergeStrategy: "cherry_pick",
              }),
              durationMs: Date.now() - start,
              metadata: {
                evidenceKind: "spawn_worktree_apply",
                changedFiles: changes.changedFiles,
                mergeStrategy: "cherry_pick",
              },
            };
          }
          const adoptedCommit = await commitChildChanges(worktreeDir);
          try {
            await execFileAsync("git", ["cherry-pick", "--no-commit", adoptedCommit], {
              cwd: parentRoot,
              maxBuffer: 4 * 1024 * 1024,
            });
          } catch (err) {
            const conflicts = await conflictedFiles(parentRoot);
            await abortCherryPick(parentRoot);
            return {
              status: "error",
              errorCode: "cherry_pick_conflict",
              errorMessage: `child worktree commit could not be cherry-picked cleanly: ${
                conflicts.join(", ") || (err instanceof Error ? err.message : String(err))
              }`,
              output: outputFor({
                action: input.action,
                spawnDir,
                worktreeDir,
                changes,
                diff: diff.text,
                truncated: diff.truncated,
                applied: false,
                cleanedUp: false,
                mergeStrategy: "cherry_pick",
                adoptedCommit,
                conflictedFiles: conflicts,
              }),
              durationMs: Date.now() - start,
              metadata: {
                evidenceKind: "spawn_worktree_apply_conflict",
                conflictedFiles: conflicts,
                adoptedCommit,
                mergeStrategy: "cherry_pick",
              },
            };
          }

          const shouldCleanup = input.cleanup === true;
          if (shouldCleanup) {
            await cleanupSpawnWorktree(parentRoot, spawnDir, worktreeDir);
          }
          ctx.emitAgentEvent?.({
            type: "spawn_worktree_apply",
            action: input.action,
            spawnDir,
            changedFiles: changes.changedFiles,
            cleanedUp: shouldCleanup,
            adoptedCommit,
            mergeStrategy: "cherry_pick",
          });
          ctx.staging.stageAuditEvent("spawn_worktree_apply", {
            spawnDir,
            changedFiles: changes.changedFiles,
            cleanedUp: shouldCleanup,
            adoptedCommit,
            mergeStrategy: "cherry_pick",
          });
          return {
            status: "ok",
            output: outputFor({
              action: input.action,
              spawnDir,
              worktreeDir,
              changes,
              diff: diff.text,
              truncated: diff.truncated,
              applied: changes.changedFiles.length > 0,
              cleanedUp: shouldCleanup,
              mergeStrategy: "cherry_pick",
              adoptedCommit,
            }),
            durationMs: Date.now() - start,
            metadata: {
              evidenceKind: "spawn_worktree_apply",
              changedFiles: changes.changedFiles,
              adoptedCommit,
              mergeStrategy: "cherry_pick",
            },
          };
        }

        if (changes.changedFiles.length === 0) {
          const shouldCleanup = input.cleanup === true;
          if (shouldCleanup) {
            await cleanupSpawnWorktree(parentRoot, spawnDir, worktreeDir);
          }
          return {
            status: "ok",
            output: outputFor({
              action: input.action,
              spawnDir,
              worktreeDir,
              changes,
              diff: diff.text,
              truncated: diff.truncated,
              applied: false,
              cleanedUp: shouldCleanup,
              mergeStrategy: "copy",
            }),
            durationMs: Date.now() - start,
            metadata: {
              evidenceKind: "spawn_worktree_apply",
              changedFiles: changes.changedFiles,
              mergeStrategy: "copy",
            },
          };
        }

        for (const relPath of changes.deletedFiles) {
          await removeParentFile(parentRoot, relPath);
        }
        for (const relPath of [...changes.modifiedFiles, ...changes.createdFiles]) {
          await copyChildFile(parentRoot, worktreeDir, relPath);
        }

        const shouldCleanup = input.cleanup === true;
        if (shouldCleanup) {
          await cleanupSpawnWorktree(parentRoot, spawnDir, worktreeDir);
        }
        ctx.emitAgentEvent?.({
          type: "spawn_worktree_apply",
          action: input.action,
          spawnDir,
          changedFiles: changes.changedFiles,
          cleanedUp: shouldCleanup,
          mergeStrategy: "copy",
        });
        ctx.staging.stageAuditEvent("spawn_worktree_apply", {
          spawnDir,
          changedFiles: changes.changedFiles,
          cleanedUp: shouldCleanup,
          mergeStrategy: "copy",
        });
        return {
          status: "ok",
          output: outputFor({
            action: input.action,
            spawnDir,
            worktreeDir,
            changes,
            diff: diff.text,
            truncated: diff.truncated,
            applied: true,
            cleanedUp: shouldCleanup,
            mergeStrategy: "copy",
          }),
          durationMs: Date.now() - start,
          metadata: {
            evidenceKind: "spawn_worktree_apply",
            changedFiles: changes.changedFiles,
            mergeStrategy: "copy",
          },
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
