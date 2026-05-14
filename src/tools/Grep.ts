/**
 * Grep — ripgrep-backed content search.
 *
 * Falls back to `grep -rn` when `rg` isn't installed, so this works in
 * minimal node:22-alpine environments.
 *
 * T1-03b: search base + relative-path output root come from the
 * per-call effective Workspace (`ctx.spawnWorkspace ?? defaultWorkspace`).
 */

import { execFile } from "node:child_process";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";
import {
  isIncognitoMemoryMode,
  isProtectedMemoryPath,
  protectedMemoryError,
} from "../util/memoryMode.js";

export interface GrepInput {
  pattern: string;
  /** Workspace-relative directory; default workspace root. */
  path?: string;
  /** Case-insensitive match. */
  caseInsensitive?: boolean;
  /** File-name glob filter (e.g. "*.ts"). */
  glob?: string;
  /** Only return file paths, no content. */
  filesOnly?: boolean;
}

export interface GrepMatch {
  file: string;
  line: number;
  text: string;
}

export interface GrepOutput {
  mode: "content" | "files_only";
  matches?: GrepMatch[];
  files?: string[];
  truncated: boolean;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    pattern: { type: "string", description: "Regex pattern (ripgrep flavour)." },
    path: { type: "string", description: "Workspace-relative search root." },
    caseInsensitive: { type: "boolean" },
    glob: { type: "string", description: "File-name glob (e.g. '*.ts')." },
    filesOnly: { type: "boolean", description: "Return file paths only, no matching lines." },
  },
  required: ["pattern"],
} as const;

const MAX_MATCHES = 2000;

export function makeGrepTool(workspaceRoot: string): Tool<GrepInput, GrepOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "Grep",
    description:
      "Content search (ripgrep when available, otherwise grep -rn). Returns matches with file path + line number, or files-only listing when filesOnly=true.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    validate(input) {
      if (!input || typeof input.pattern !== "string" || input.pattern.length === 0) {
        return "`pattern` is required";
      }
      return null;
    },
    async execute(input: GrepInput, ctx: ToolContext): Promise<ToolResult<GrepOutput>> {
      const start = Date.now();
      const incognito = isIncognitoMemoryMode(ctx.memoryMode);
      if (incognito && isProtectedMemoryPath(input.path)) {
        return {
          status: "permission_denied",
          errorCode: "incognito_memory_blocked",
          errorMessage: protectedMemoryError(input.path),
          durationMs: Date.now() - start,
        };
      }
      const ws = ctx.spawnWorkspace ?? defaultWorkspace;
      let base: string;
      try {
        base = input.path ? ws.resolve(input.path) : ws.root;
      } catch (err) {
        return errorResult(err, start);
      }
      const hasRg = await binaryExists("rg");
      return new Promise<ToolResult<GrepOutput>>((resolve) => {
        const { binary, args } = buildArgs({ hasRg, input, base, excludeMemory: incognito });
        execFile(binary, args, { maxBuffer: 10 * 1024 * 1024, timeout: 30_000 }, (err, stdout) => {
          // grep/rg return exit code 1 when no matches are found — not
          // an error.
          const exitCode = (err as { code?: number } | null)?.code;
          if (err && exitCode !== 1) {
            resolve(errorResult(err, start));
            return;
          }
          const parsed = parseOutput({ stdout, input, workspaceRoot: ws.root, excludeMemory: incognito });
          resolve({
            status: "ok",
            output: parsed,
            durationMs: Date.now() - start,
          });
        });
      });
    },
  };
}

function buildArgs(opts: {
  hasRg: boolean;
  input: GrepInput;
  base: string;
  excludeMemory?: boolean;
}): { binary: string; args: string[] } {
  const { hasRg, input, base, excludeMemory = false } = opts;
  if (hasRg) {
    const args = ["--no-heading", "--line-number", "--color=never"];
    if (input.caseInsensitive) args.push("-i");
    if (input.filesOnly) args.push("-l");
    if (input.glob) args.push("-g", input.glob);
    if (excludeMemory) {
      args.push(
        "-g", "!memory/**",
        "-g", "!MEMORY.md",
        "-g", "!SCRATCHPAD.md",
        "-g", "!WORKING.md",
        "-g", "!TASK-QUEUE.md",
      );
    }
    args.push(input.pattern, base);
    return { binary: "rg", args };
  }
  const args = ["-rnH", "--color=never"];
  if (input.caseInsensitive) args.push("-i");
  if (input.filesOnly) args.push("-l");
  if (input.glob) args.push("--include", input.glob);
  args.push("-e", input.pattern, base);
  return { binary: "grep", args };
}

function parseOutput(opts: {
  stdout: string;
  input: GrepInput;
  workspaceRoot: string;
  excludeMemory?: boolean;
}): GrepOutput {
  const { stdout, input, workspaceRoot, excludeMemory = false } = opts;
  const lines = stdout.split("\n").filter((l) => l.length > 0);
  if (input.filesOnly) {
    const files = lines
      .map((l) => path.relative(workspaceRoot, l))
      .filter((file) => !excludeMemory || !isProtectedMemoryPath(file))
      .slice(0, MAX_MATCHES);
    return {
      mode: "files_only",
      files,
      truncated: lines.length > MAX_MATCHES,
    };
  }
  const matches: GrepMatch[] = [];
  for (const l of lines.slice(0, MAX_MATCHES)) {
    // Format: path:line:text
    const first = l.indexOf(":");
    const second = l.indexOf(":", first + 1);
    if (first < 0 || second < 0) continue;
    const file = path.relative(workspaceRoot, l.slice(0, first));
    if (excludeMemory && isProtectedMemoryPath(file)) continue;
    const lineNo = Number.parseInt(l.slice(first + 1, second), 10);
    const text = l.slice(second + 1);
    if (!Number.isFinite(lineNo)) continue;
    matches.push({ file, line: lineNo, text });
  }
  return {
    mode: "content",
    matches,
    truncated: lines.length > MAX_MATCHES,
  };
}

function binaryExists(name: string): Promise<boolean> {
  return new Promise((resolve) => {
    execFile("sh", ["-c", `command -v ${name} >/dev/null 2>&1`], (err) => {
      resolve(!err);
    });
  });
}
