import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";

const execFileAsync = promisify(execFile);

export interface GitDiffInput {
  cwd?: string;
  staged?: boolean;
  statOnly?: boolean;
  maxBytes?: number;
}

export interface GitDiffOutput {
  cwd: string;
  changedFiles: string[];
  stat: string;
  diff: string;
  truncated: boolean;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    cwd: { type: "string", description: "Workspace-relative repository directory." },
    staged: { type: "boolean", description: "Inspect staged changes only." },
    statOnly: { type: "boolean", description: "Return only file list/stat, omit full diff." },
    maxBytes: { type: "integer", minimum: 1000, description: "Maximum diff bytes returned." },
  },
} as const;

const DEFAULT_MAX_BYTES = 256 * 1024;
const MAX_BYTES = 1024 * 1024;

function parseChangedFiles(status: string): string[] {
  const files = new Set<string>();
  for (const line of status.split("\n")) {
    if (!line.trim()) continue;
    const raw = line.slice(3).trim();
    if (!raw) continue;
    const renamed = raw.includes(" -> ") ? raw.split(" -> ").at(-1) ?? raw : raw;
    files.add(renamed.replace(/^"|"$/g, ""));
  }
  return [...files].sort();
}

function truncate(text: string, maxBytes: number): { text: string; truncated: boolean } {
  const buf = Buffer.from(text, "utf8");
  if (buf.byteLength <= maxBytes) return { text, truncated: false };
  return {
    text: buf.subarray(0, maxBytes).toString("utf8"),
    truncated: true,
  };
}

async function git(cwd: string, args: string[]): Promise<string> {
  const { stdout } = await execFileAsync("git", args, {
    cwd,
    maxBuffer: 2 * 1024 * 1024,
  });
  return stdout;
}

export function makeGitDiffTool(workspaceRoot: string): Tool<GitDiffInput, GitDiffOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "GitDiff",
    description:
      "Capture structured git diff evidence for coding work. Use after editing and before final verification so the runtime can see exactly which files changed.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    mutatesWorkspace: false,
    isConcurrencySafe: true,
    async execute(input: GitDiffInput, ctx: ToolContext): Promise<ToolResult<GitDiffOutput>> {
      const start = Date.now();
      try {
        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        const cwd = input.cwd ? ws.resolve(input.cwd) : ws.root;
        const maxBytes = Math.min(MAX_BYTES, Math.max(1000, input.maxBytes ?? DEFAULT_MAX_BYTES));
        const status = await git(cwd, ["status", "--porcelain", "--untracked-files=all"]);
        const changedFiles = parseChangedFiles(status);
        const diffArgs = ["diff", "--no-ext-diff"];
        const statArgs = ["diff", "--stat", "--no-ext-diff"];
        if (input.staged === true) {
          diffArgs.push("--cached");
          statArgs.push("--cached");
        }
        const [rawStat, rawDiff] = await Promise.all([
          git(cwd, statArgs),
          input.statOnly === true ? Promise.resolve("") : git(cwd, diffArgs),
        ]);
        const clipped = truncate(rawDiff, maxBytes);
        return {
          status: "ok",
          output: {
            cwd: path.relative(ws.root, cwd) || ".",
            changedFiles,
            stat: rawStat,
            diff: clipped.text,
            truncated: clipped.truncated,
          },
          durationMs: Date.now() - start,
          metadata: {
            evidenceKind: "diff",
            changedFiles,
          },
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
