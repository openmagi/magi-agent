/**
 * Bash — run a shell command with stdout/stderr capture.
 *
 * Phase 1b: execFile-style child process (no shell injection surface
 * because we ARE the shell); cwd scoped to workspace; timeout default
 * 120s hard-capped at 600s; output limited to 512 KB.
 *
 * T1-03b: the spawned process's cwd is resolved per call against
 * ctx.spawnWorkspace (if present) rather than the factory-captured
 * parent workspace. This closes the PRE-01 gap where a spawned child
 * with Bash in allowed_tools could still `cd ..` out of its subdir
 * because the cwd was pinned to the parent PVC root.
 *
 * Per-tool network / fs capability scoping (IronClaw-style) is a
 * post-Phase-3 hardening item — see design doc §12 open Q11.
 */

import { spawn } from "node:child_process";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";
import { withClawyBinPath } from "../util/shellPath.js";

export interface BashInput {
  command: string;
  /** Timeout in ms; default 120_000, hard cap 600_000. */
  timeoutMs?: number;
  /** Workspace-relative cwd; default workspace root. */
  cwd?: string;
}

export interface BashOutput {
  exitCode: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
  truncated: boolean;
  durationMs: number;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    command: { type: "string", description: "Shell command line. Executed via /bin/sh -c." },
    timeoutMs: { type: "integer", minimum: 100, description: "Timeout in ms (max 600000)." },
    cwd: { type: "string", description: "Workspace-relative working directory." },
  },
  required: ["command"],
} as const;

const MAX_OUTPUT_BYTES = 512 * 1024;
const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_TIMEOUT_MS = 600_000;

export function makeBashTool(workspaceRoot: string): Tool<BashInput, BashOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "Bash",
    description:
      "Run a shell command. cwd is the workspace root by default. Output is captured and truncated at ~512 KB per stream. Prefer FileRead/FileWrite/FileEdit/Grep/Glob for file operations — this tool is for anything else.",
    inputSchema: INPUT_SCHEMA,
    permission: "execute",
    dangerous: false,
    validate(input) {
      if (!input || typeof input.command !== "string" || input.command.length === 0) {
        return "`command` is required";
      }
      return null;
    },
    async execute(input: BashInput, ctx: ToolContext): Promise<ToolResult<BashOutput>> {
      const start = Date.now();
      const timeoutMs = Math.min(MAX_TIMEOUT_MS, input.timeoutMs ?? DEFAULT_TIMEOUT_MS);
      const ws = ctx.spawnWorkspace ?? defaultWorkspace;
      let cwd: string;
      try {
        cwd = input.cwd ? ws.resolve(input.cwd) : ws.root;
      } catch (err) {
        return errorResult(err, start);
      }

      return new Promise<ToolResult<BashOutput>>((resolve) => {
        try {
          const child = spawn("/bin/sh", ["-c", input.command], {
            cwd,
            env: { ...withClawyBinPath(process.env), PWD: cwd },
            stdio: ["ignore", "pipe", "pipe"],
          });

          let stdout = "";
          let stderr = "";
          let truncated = false;
          const capture = (chunk: Buffer, which: "stdout" | "stderr"): void => {
            const current = which === "stdout" ? stdout : stderr;
            if (current.length >= MAX_OUTPUT_BYTES) {
              truncated = true;
              return;
            }
            const room = MAX_OUTPUT_BYTES - current.length;
            const piece = chunk.toString("utf8");
            if (which === "stdout") stdout += piece.slice(0, room);
            else stderr += piece.slice(0, room);
            if (piece.length > room) truncated = true;
          };
          child.stdout.on("data", (c: Buffer) => capture(c, "stdout"));
          child.stderr.on("data", (c: Buffer) => capture(c, "stderr"));

          const timeout = setTimeout(() => {
            child.kill("SIGTERM");
            setTimeout(() => child.kill("SIGKILL"), 3_000).unref();
          }, timeoutMs);

          ctx.abortSignal.addEventListener(
            "abort",
            () => child.kill("SIGTERM"),
            { once: true },
          );

          child.on("close", (code, signal) => {
            clearTimeout(timeout);
            resolve({
              status: code === 0 ? "ok" : "error",
              output: {
                exitCode: code,
                signal,
                stdout,
                stderr,
                truncated,
                durationMs: Date.now() - start,
              },
              errorCode: code === 0 ? undefined : `exit_${code}`,
              errorMessage: code === 0 ? undefined : stderr.slice(0, 500) || `exit ${code}`,
              durationMs: Date.now() - start,
            });
          });
          child.on("error", (err) => {
            clearTimeout(timeout);
            resolve(errorResult(err, start));
          });
        } catch (err) {
          resolve(errorResult(err, start));
        }
      });
    },
  };
}
