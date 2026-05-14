/**
 * ShadowGit — independent Git DAG for workspace checkpointing.
 *
 * Uses GIT_DIR=.shadow-git + GIT_WORK_TREE=workspace so the shadow
 * repo tracks the same files as the real .git but keeps a separate
 * history. Lazy-init on first checkpoint.
 */

import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { Utf8StreamCapture } from "../util/Utf8StreamCapture.js";

export interface ShadowGitOptions {
  workspaceRoot: string;
  largeFileThreshold?: number;
  timeoutMs?: number;
}

export interface CheckpointMeta {
  toolName: string;
  turnId: string;
  sessionKey: string;
  timestamp: number;
  filesHint?: string[];
}

export interface CheckpointEntry {
  sha: string;
  fullSha: string;
  message: string;
  timestamp: string;
  turnId: string;
  sessionKey: string;
  toolName: string | null;
  filesChanged: string[];
}

export interface DiffResult {
  diff: string;
  stats: { additions: number; deletions: number; filesModified: number };
}

const LARGE_FILE_THRESHOLD_DEFAULT = 5 * 1024 * 1024;
const TIMEOUT_DEFAULT = 5_000;

const DEFAULT_EXCLUDES = `# Shadow checkpoint exclusions
.git/
.shadow-git/
node_modules/
.cache/
.next/
dist/
build/
*.sqlite
*.sqlite-journal
.qmd/
*.mp4
*.mov
*.zip
*.tar.gz
`;

interface SpawnResult {
  code: number;
  stdout: string;
  stderr: string;
}

function shadowEnv(workspaceRoot: string): Record<string, string> {
  return {
    ...process.env as Record<string, string>,
    GIT_DIR: path.join(workspaceRoot, ".shadow-git"),
    GIT_WORK_TREE: workspaceRoot,
    GIT_AUTHOR_NAME: "magi-checkpoint",
    GIT_AUTHOR_EMAIL: "checkpoint@openmagi.ai",
    GIT_COMMITTER_NAME: "magi-checkpoint",
    GIT_COMMITTER_EMAIL: "checkpoint@openmagi.ai",
  };
}

export async function runShadowGit(
  workspaceRoot: string,
  args: readonly string[],
  timeoutMs = TIMEOUT_DEFAULT,
  envOverride?: Record<string, string>,
): Promise<SpawnResult> {
  return new Promise((resolve) => {
    const child = spawn("git", [...args], {
      cwd: workspaceRoot,
      stdio: ["ignore", "pipe", "pipe"],
      env: envOverride ?? shadowEnv(workspaceRoot),
    });
    const stdout = new Utf8StreamCapture();
    const stderr = new Utf8StreamCapture();
    child.stdout.on("data", (d: Buffer) => stdout.write(d));
    child.stderr.on("data", (d: Buffer) => stderr.write(d));

    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      resolve({ code: 124, stdout: stdout.end(), stderr: "timeout" });
    }, timeoutMs);

    child.on("error", () => {
      clearTimeout(timer);
      resolve({ code: 127, stdout: stdout.end(), stderr: stderr.end() });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ code: code ?? 1, stdout: stdout.end(), stderr: stderr.end() });
    });
  });
}

export class ShadowGit {
  private readonly root: string;
  private readonly shadowDir: string;
  private readonly largeFileThreshold: number;
  private readonly timeoutMs: number;
  private initialized = false;

  constructor(opts: ShadowGitOptions) {
    this.root = opts.workspaceRoot;
    this.shadowDir = path.join(opts.workspaceRoot, ".shadow-git");
    this.largeFileThreshold = opts.largeFileThreshold ?? LARGE_FILE_THRESHOLD_DEFAULT;
    this.timeoutMs = opts.timeoutMs ?? TIMEOUT_DEFAULT;
  }

  async ensureInitialized(): Promise<void> {
    if (this.initialized) return;

    try {
      await fs.access(path.join(this.shadowDir, "HEAD"));
      this.initialized = true;
      return;
    } catch {
      // not yet initialized
    }

    // Create .shadow-git as a non-bare repo directory structure
    // (git init with GIT_DIR set, pointing to the shadow dir)
    await fs.mkdir(this.shadowDir, { recursive: true });
    const init = await runShadowGit(
      this.root,
      ["init"],
      this.timeoutMs,
    );
    if (init.code !== 0) {
      throw new Error(`shadow-git init failed: ${init.stderr}`);
    }

    // Write default excludes BEFORE first add
    const excludeDir = path.join(this.shadowDir, "info");
    await fs.mkdir(excludeDir, { recursive: true });
    await fs.writeFile(path.join(excludeDir, "exclude"), DEFAULT_EXCLUDES, "utf8");

    // Set gc.auto=0
    await runShadowGit(this.root, ["config", "gc.auto", "0"], this.timeoutMs);

    // Initial commit
    await runShadowGit(this.root, ["add", "-A"], this.timeoutMs);
    await runShadowGit(
      this.root,
      ["commit", "--allow-empty", "-m", "initial workspace state"],
      this.timeoutMs,
    );

    // Add .shadow-git to real .git/info/exclude if real git exists
    await this.excludeFromRealGit();

    this.initialized = true;
  }

  async createCheckpoint(meta: CheckpointMeta): Promise<string | null> {
    await this.ensureInitialized();

    // Handle large files before staging
    await this.handleLargeFiles();

    // Stage all changes
    const add = await runShadowGit(this.root, ["add", "-A"], this.timeoutMs);
    if (add.code !== 0) return null;

    // Check if anything to commit
    const status = await runShadowGit(
      this.root,
      ["status", "--porcelain"],
      this.timeoutMs,
    );
    const changedLines = status.stdout
      .split("\n")
      .filter((l) => l.trim().length > 0);
    if (changedLines.length === 0) return null;

    // Build commit message (blank line separates subject from body)
    const filesHint = meta.filesHint?.join(", ") ?? "";
    const filesLine = filesHint || changedLines.map((l) => l.slice(3).trim()).join(", ");
    const msg =
      `checkpoint: ${meta.toolName} ${filesHint}`.trim() +
      "\n\n" +
      `turn: ${meta.turnId} | session: ${meta.sessionKey}\n` +
      `files: ${filesLine}`;

    const commit = await runShadowGit(
      this.root,
      ["commit", "-m", msg],
      this.timeoutMs,
    );
    if (commit.code !== 0) return null;

    const sha = await runShadowGit(
      this.root,
      ["rev-parse", "HEAD"],
      this.timeoutMs,
    );
    return sha.stdout.trim() || null;
  }

  async listCheckpoints(opts?: {
    limit?: number;
    offset?: number;
  }): Promise<CheckpointEntry[]> {
    await this.ensureInitialized();

    const limit = opts?.limit ?? 50;
    const offset = opts?.offset ?? 0;
    const total = limit + offset;

    const log = await runShadowGit(
      this.root,
      [
        "log",
        `--max-count=${total}`,
        "--format=%H%n%h%n%s%n%aI%n%b%n---ENTRY---",
      ],
      this.timeoutMs,
    );
    if (log.code !== 0) return [];

    const entries: CheckpointEntry[] = [];
    const blocks = log.stdout.split("---ENTRY---\n").filter((b) => b.trim());

    for (const block of blocks) {
      const lines = block.split("\n");
      if (lines.length < 4) continue;

      const fullSha = lines[0]!.trim();
      const sha = lines[1]!.trim();
      const subject = lines[2]!.trim();
      const timestamp = lines[3]!.trim();

      // Parse body for turn/session/files
      const body = lines.slice(4).join("\n");
      const turnMatch = body.match(/turn:\s*(\S+)/);
      const sessionMatch = body.match(/session:\s*(\S+)/);
      const filesMatch = body.match(/files:\s*(.+)/);
      const toolMatch = subject.match(/^checkpoint:\s*(\S+)/);

      entries.push({
        sha,
        fullSha,
        message: subject,
        timestamp,
        turnId: turnMatch?.[1] ?? "",
        sessionKey: sessionMatch?.[1] ?? "",
        toolName: toolMatch?.[1] ?? null,
        filesChanged: filesMatch?.[1]?.split(",").map((f) => f.trim()).filter(Boolean) ?? [],
      });
    }

    return entries.slice(offset, offset + limit);
  }

  async diffCheckpoints(
    fromHash: string,
    toHash: string,
  ): Promise<string> {
    await this.ensureInitialized();

    const result = await runShadowGit(
      this.root,
      ["diff", fromHash, toHash],
      this.timeoutMs,
    );
    return result.stdout;
  }

  async restoreCheckpoint(sha: string): Promise<{
    newSha: string;
    restoredFiles: string[];
  }> {
    await this.ensureInitialized();

    // Safety checkpoint before restore (allow-empty to always capture state)
    await runShadowGit(this.root, ["add", "-A"], this.timeoutMs);
    const safetyMsg =
      "checkpoint: restore-safety\n\npre-restore snapshot before restoring to " +
      sha.slice(0, 8);
    const safetyCommit = await runShadowGit(
      this.root,
      ["commit", "--allow-empty", "-m", safetyMsg],
      this.timeoutMs,
    );
    let safetySha: string | null = null;
    if (safetyCommit.code === 0) {
      const rev = await runShadowGit(this.root, ["rev-parse", "HEAD"], this.timeoutMs);
      safetySha = rev.stdout.trim();
    }

    // Get file list at target sha
    const lsTree = await runShadowGit(
      this.root,
      ["ls-tree", "-r", "--name-only", sha],
      this.timeoutMs,
    );
    const targetFiles = lsTree.stdout
      .split("\n")
      .filter((f) => f.trim().length > 0);

    // Restore via checkout
    const checkout = await runShadowGit(
      this.root,
      ["checkout", sha, "--", "."],
      this.timeoutMs,
    );
    if (checkout.code !== 0) {
      throw new Error(`restore failed: ${checkout.stderr}`);
    }

    // Create post-restore checkpoint
    const postSha = await this.createCheckpoint({
      toolName: "restore",
      turnId: "system",
      sessionKey: "system",
      timestamp: Date.now(),
      filesHint: [`restored-to-${sha.slice(0, 8)}`],
    });

    return {
      newSha: postSha ?? safetySha ?? sha,
      restoredFiles: targetFiles,
    };
  }

  async getStorageUsage(): Promise<string> {
    const result = await runShadowGit(
      this.root,
      ["count-objects", "-vH"],
      this.timeoutMs,
    );
    if (result.code !== 0) return "unknown";
    const sizeLine = result.stdout
      .split("\n")
      .find((l) => l.startsWith("size-pack:"));
    return sizeLine ?? "unknown";
  }

  private async handleLargeFiles(): Promise<void> {
    const status = await runShadowGit(
      this.root,
      ["status", "--porcelain"],
      this.timeoutMs,
    );
    const changed = status.stdout
      .split("\n")
      .filter((l) => l.trim().length > 0)
      .map((l) => l.slice(3).trim());

    const ignorePath = path.join(this.root, ".shadowgitignore");
    let ignoreContent = "";
    try {
      ignoreContent = await fs.readFile(ignorePath, "utf8");
    } catch {
      // no ignore file yet
    }

    let modified = false;
    for (const file of changed) {
      const fullPath = path.join(this.root, file);
      try {
        const stat = await fs.stat(fullPath);
        if (stat.size > this.largeFileThreshold) {
          if (!ignoreContent.includes(file)) {
            ignoreContent += `${file}\n`;
            modified = true;
          }
        }
      } catch {
        // file may have been deleted
      }
    }

    if (modified) {
      await fs.writeFile(ignorePath, ignoreContent, "utf8");
      // Update exclude to include shadowgitignore entries
      const excludePath = path.join(this.shadowDir, "info", "exclude");
      let excludeContent = DEFAULT_EXCLUDES;
      try {
        excludeContent = await fs.readFile(excludePath, "utf8");
      } catch {
        // use defaults
      }
      const newEntries = ignoreContent
        .split("\n")
        .filter((l) => l.trim() && !excludeContent.includes(l));
      if (newEntries.length > 0) {
        await fs.writeFile(
          excludePath,
          excludeContent + "\n" + newEntries.join("\n") + "\n",
          "utf8",
        );
      }
    }
  }

  private async excludeFromRealGit(): Promise<void> {
    const realGitDir = path.join(this.root, ".git");
    try {
      await fs.access(realGitDir);
    } catch {
      return; // no real .git
    }
    const excludePath = path.join(realGitDir, "info", "exclude");
    try {
      await fs.mkdir(path.join(realGitDir, "info"), { recursive: true });
      let content = "";
      try {
        content = await fs.readFile(excludePath, "utf8");
      } catch {
        // no exclude file yet
      }
      if (!content.includes(".shadow-git")) {
        await fs.writeFile(excludePath, content + "\n.shadow-git\n", "utf8");
      }
    } catch {
      // best effort
    }
  }
}
