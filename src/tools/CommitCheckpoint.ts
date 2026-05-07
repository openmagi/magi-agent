/**
 * CommitCheckpoint — builtin tool for the Coding Discipline subsystem
 * (docs/plans/2026-04-20-coding-discipline-design.md §"Layer 2").
 *
 * Runs `git add -A && git commit -m <message>` inside the bot
 * workspace. Returns the new commit sha + file count, or an error
 * when git is unavailable / the session has `discipline.git === false`
 * / the tree is clean.
 *
 * No shell is invoked — `spawn` with argv[] prevents message-injection
 * attacks regardless of what the LLM puts in `message`.
 *
 * Hook-gated: when discipline.git is OFF for the session, the tool
 * errors early with "discipline.git is off for this session" so
 * callers see a clear signal rather than a silent no-op.
 */

import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type { Discipline } from "../Session.js";
import type { DisciplineSessionCounter } from "../hooks/builtin/disciplineHook.js";
import { Utf8StreamCapture } from "../util/Utf8StreamCapture.js";

export interface CommitCheckpointInput {
  message: string;
}

export interface CommitCheckpointOutput {
  commitSha: string;
  filesChanged: number;
  message: string;
}

export interface CommitCheckpointAgent {
  getSessionDiscipline(sessionKey: string): Discipline | null;
  getSessionCounter(sessionKey: string): DisciplineSessionCounter;
}

export interface CommitCheckpointOptions {
  workspaceRoot: string;
  agent: CommitCheckpointAgent;
  /** Test seam — overrides `Date.now()`. */
  now?: () => number;
}

interface SpawnResult {
  code: number;
  stdout: string;
  stderr: string;
}

/**
 * Run a git subcommand in `cwd`. Returns the captured stdout/stderr
 * + exit code. Never throws on non-zero exit — the caller decides
 * whether that's an error (e.g. `git commit` with no staged changes
 * returns 1 but is not a tool failure).
 */
export async function runGit(
  cwd: string,
  args: readonly string[],
): Promise<SpawnResult> {
  return new Promise((resolve) => {
    const child = spawn("git", ["-C", cwd, ...args], {
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        GIT_AUTHOR_NAME: process.env["GIT_AUTHOR_NAME"] ?? "magi-bot",
        GIT_AUTHOR_EMAIL:
          process.env["GIT_AUTHOR_EMAIL"] ?? "bot@magi.local",
        GIT_COMMITTER_NAME:
          process.env["GIT_COMMITTER_NAME"] ?? "magi-bot",
        GIT_COMMITTER_EMAIL:
          process.env["GIT_COMMITTER_EMAIL"] ?? "bot@magi.local",
      },
    });
    const stdout = new Utf8StreamCapture();
    const stderr = new Utf8StreamCapture();
    child.stdout.on("data", (d: Buffer) => stdout.write(d));
    child.stderr.on("data", (d: Buffer) => stderr.write(d));
    child.on("error", () =>
      resolve({ code: 127, stdout: stdout.end(), stderr: stderr.end() }),
    );
    child.on("close", (code) =>
      resolve({ code: code ?? 1, stdout: stdout.end(), stderr: stderr.end() }),
    );
  });
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    message: {
      type: "string",
      description:
        "Commit message. Prefer imperative mood, 72 chars or less for the subject line.",
    },
  },
  required: ["message"],
} as const;

export function makeCommitCheckpointTool(
  opts: CommitCheckpointOptions,
): Tool<CommitCheckpointInput, CommitCheckpointOutput> {
  const now = opts.now ?? Date.now;
  return {
    name: "CommitCheckpoint",
    description:
      "Stage + commit all current workspace changes with the provided message. Returns the new commit sha and the number of files changed. Use at logical milestones (after a test passes, after a refactor, before switching tasks).",
    inputSchema: INPUT_SCHEMA,
    permission: "execute",
    // Always-registered, hook-gated — errors early when
    // discipline.git is off so the LLM sees a clear signal.
    kind: "core",
    validate(input) {
      if (!input || typeof input.message !== "string") {
        return "`message` is required";
      }
      const trimmed = input.message.trim();
      if (trimmed.length === 0) return "`message` must be non-empty";
      if (trimmed.length > 4096) return "`message` too long (max 4096 chars)";
      return null;
    },
    async execute(
      input: CommitCheckpointInput,
      ctx: ToolContext,
    ): Promise<ToolResult<CommitCheckpointOutput>> {
      const started = Date.now();
      const discipline = opts.agent.getSessionDiscipline(ctx.sessionKey);
      if (!discipline || !discipline.git) {
        return {
          status: "permission_denied",
          errorCode: "DISCIPLINE_GIT_OFF",
          errorMessage: "discipline.git is off for this session",
          durationMs: Date.now() - started,
        };
      }

      // Scope the cwd to the bot workspace. spawnWorkspace (subagent
      // context) takes precedence so children commit to their isolated
      // subtree rather than the parent workspace.
      const cwd = ctx.spawnWorkspace
        ? ctx.spawnWorkspace.root
        : opts.workspaceRoot;

      // Confirm a git repo exists at cwd. Fail fast with a clear error
      // rather than letting git complain.
      try {
        await fs.access(path.join(cwd, ".git"));
      } catch {
        return {
          status: "error",
          errorCode: "GIT_NOT_INITIALIZED",
          errorMessage:
            "No git repository at workspace root. Agent.start initialises one when discipline.git is true; if it didn't, the `git` binary may be missing from the container.",
          durationMs: Date.now() - started,
        };
      }

      // Stage everything.
      const add = await runGit(cwd, ["add", "-A"]);
      if (add.code !== 0) {
        return {
          status: "error",
          errorCode: "GIT_ADD_FAILED",
          errorMessage: `git add -A failed (exit ${add.code}): ${add.stderr.slice(0, 500)}`,
          durationMs: Date.now() - started,
        };
      }

      // Check whether there is anything to commit.
      const status = await runGit(cwd, ["status", "--porcelain"]);
      if (status.code !== 0) {
        return {
          status: "error",
          errorCode: "GIT_STATUS_FAILED",
          errorMessage: `git status failed (exit ${status.code}): ${status.stderr.slice(0, 500)}`,
          durationMs: Date.now() - started,
        };
      }
      const changedLines = status.stdout
        .split("\n")
        .filter((l) => l.trim().length > 0);
      if (changedLines.length === 0) {
        return {
          status: "empty",
          errorCode: "NOTHING_TO_COMMIT",
          errorMessage: "No staged or unstaged changes to commit.",
          durationMs: Date.now() - started,
        };
      }

      // Commit.
      const commit = await runGit(cwd, ["commit", "-m", input.message]);
      if (commit.code !== 0) {
        return {
          status: "error",
          errorCode: "GIT_COMMIT_FAILED",
          errorMessage: `git commit failed (exit ${commit.code}): ${commit.stderr.slice(0, 500)}`,
          durationMs: Date.now() - started,
        };
      }
      const sha = await runGit(cwd, ["rev-parse", "HEAD"]);
      const commitSha = sha.stdout.trim();

      // Reset session counter dirty-file tracking — the checkpoint
      // absorbs everything into a clean state.
      const counter = opts.agent.getSessionCounter(ctx.sessionKey);
      counter.lastCommitAt = now();
      counter.dirtyFilesSinceCommit = 0;

      ctx.staging.stageAuditEvent("checkpoint_committed", {
        commitSha,
        filesChanged: changedLines.length,
      });

      return {
        status: "ok",
        output: {
          commitSha,
          filesChanged: changedLines.length,
          message: input.message,
        },
        durationMs: Date.now() - started,
      };
    },
  };
}
