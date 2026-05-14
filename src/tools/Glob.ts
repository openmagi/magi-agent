/**
 * Glob — workspace file pattern match.
 *
 * Uses shell-style globbing via `sh -c 'printf %s\n <pattern>'` so we
 * don't pull in a glob lib. Respects the workspace boundary.
 *
 * T1-03b: the search base + relative-path output root come from the
 * per-call effective Workspace (`ctx.spawnWorkspace ?? defaultWorkspace`)
 * so a child sees only files inside its ephemeral spawn subdir.
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

export interface GlobInput {
  pattern: string;
  /** Workspace-relative directory to glob from; default workspace root. */
  path?: string;
}

export interface GlobOutput {
  matches: string[];
  truncated: boolean;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    pattern: { type: "string", description: "Shell glob pattern, e.g. 'src/**/*.ts'" },
    path: { type: "string", description: "Workspace-relative base directory." },
  },
  required: ["pattern"],
} as const;

const MAX_MATCHES = 500;

export function makeGlobTool(workspaceRoot: string): Tool<GlobInput, GlobOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "Glob",
    description:
      "Match files by shell glob (e.g. '**/*.ts'). Returns workspace-relative paths. Sorted by modification time desc.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    validate(input) {
      if (!input || typeof input.pattern !== "string" || input.pattern.length === 0) {
        return "`pattern` is required";
      }
      return null;
    },
    async execute(input: GlobInput, ctx: ToolContext): Promise<ToolResult<GlobOutput>> {
      const start = Date.now();
      const incognito = isIncognitoMemoryMode(ctx.memoryMode);
      if (
        incognito &&
        (isProtectedMemoryPath(input.path) || isProtectedMemoryPath(input.pattern))
      ) {
        return {
          status: "permission_denied",
          errorCode: "incognito_memory_blocked",
          errorMessage: protectedMemoryError(input.path ?? input.pattern),
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
      // Use `find` because POSIX `ls` doesn't recurse + globbing via
      // shell would require untrusted expansion. find's -path matcher
      // takes a glob.
      const posixPattern = input.pattern.includes("/")
        ? `*/${input.pattern}`
        : input.pattern;
      return new Promise<ToolResult<GlobOutput>>((resolve) => {
        execFile(
          "find",
          [base, "-type", "f", "-path", posixPattern.replace(/\*\*/g, "*"), "-printf", "%T@ %p\n"],
          { maxBuffer: 10 * 1024 * 1024, timeout: 30_000 },
          (err, stdout) => {
            // execFile gives `err.code = number` for non-zero exit; code
            // 1 from `find` usually means "no matches" for the -path
            // clause — treat as empty result.
            const exitCode = (err as { code?: number } | null)?.code;
            if (err && exitCode !== 1) {
              resolve(errorResult(err, start));
              return;
            }
            const raw = stdout
              .split("\n")
              .filter((l) => l.length > 0)
              .map((l) => {
                const space = l.indexOf(" ");
                if (space < 0) return { ts: 0, p: l };
                return { ts: Number.parseFloat(l.slice(0, space)), p: l.slice(space + 1) };
              })
              .sort((a, b) => b.ts - a.ts);
            const truncated = raw.length > MAX_MATCHES;
            const matches = raw
              .slice(0, MAX_MATCHES)
              .map((entry) => path.relative(ws.root, entry.p))
              .filter((file) => !incognito || !isProtectedMemoryPath(file));
            resolve({
              status: "ok",
              output: { matches, truncated },
              durationMs: Date.now() - start,
            });
          },
        );
      });
    },
  };
}
