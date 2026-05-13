import { spawn } from "node:child_process";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { Utf8StreamCapture } from "../util/Utf8StreamCapture.js";
import { errorResult } from "../util/toolResult.js";
import { withMagiBinPath } from "../util/shellPath.js";

export interface TestRunInput {
  command: string;
  timeoutMs?: number;
  cwd?: string;
}

export interface TestRunOutput {
  command: string;
  cwd: string;
  exitCode: number | null;
  signal: string | null;
  passed: boolean;
  stdout: string;
  stderr: string;
  truncated: boolean;
  durationMs: number;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    command: {
      type: "string",
      description: "Verification command to run, for example npm test, npm run build, pytest, or tsc.",
    },
    timeoutMs: { type: "integer", minimum: 100, description: "Timeout in ms (max 600000)." },
    cwd: { type: "string", description: "Workspace-relative working directory." },
  },
  required: ["command"],
} as const;

const MAX_OUTPUT_BYTES = 512 * 1024;
const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_TIMEOUT_MS = 600_000;

export function makeTestRunTool(workspaceRoot: string): Tool<TestRunInput, TestRunOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "TestRun",
    description:
      "Run a deterministic verification command for code changes. Use this for tests, build, lint, typecheck, smoke checks, or other acceptance checks before claiming code work is complete.",
    inputSchema: INPUT_SCHEMA,
    permission: "execute",
    dangerous: true,
    mutatesWorkspace: false,
    isConcurrencySafe: false,
    validate(input) {
      if (!input || typeof input.command !== "string" || input.command.length === 0) {
        return "`command` is required";
      }
      return null;
    },
    async execute(input: TestRunInput, ctx: ToolContext): Promise<ToolResult<TestRunOutput>> {
      const start = Date.now();
      const timeoutMs = Math.min(MAX_TIMEOUT_MS, input.timeoutMs ?? DEFAULT_TIMEOUT_MS);
      const ws = ctx.spawnWorkspace ?? defaultWorkspace;
      let cwd: string;
      try {
        cwd = input.cwd ? ws.resolve(input.cwd) : ws.root;
      } catch (err) {
        return errorResult(err, start);
      }

      return new Promise<ToolResult<TestRunOutput>>((resolve) => {
        try {
          const child = spawn("/bin/sh", ["-c", input.command], {
            cwd,
            env: { ...withMagiBinPath(process.env), PWD: cwd },
            stdio: ["ignore", "pipe", "pipe"],
          });
          const stdout = new Utf8StreamCapture(MAX_OUTPUT_BYTES);
          const stderr = new Utf8StreamCapture(MAX_OUTPUT_BYTES);
          child.stdout.on("data", (c: Buffer) => stdout.write(c));
          child.stderr.on("data", (c: Buffer) => stderr.write(c));

          const timeout = setTimeout(() => {
            child.kill("SIGTERM");
            setTimeout(() => child.kill("SIGKILL"), 3_000).unref();
          }, timeoutMs);
          ctx.abortSignal.addEventListener("abort", () => child.kill("SIGTERM"), {
            once: true,
          });

          child.on("close", (code, signal) => {
            clearTimeout(timeout);
            const stdoutText = stdout.end();
            const stderrText = stderr.end();
            const passed = code === 0;
            const output: TestRunOutput = {
              command: input.command,
              cwd,
              exitCode: code,
              signal,
              passed,
              stdout: stdoutText,
              stderr: stderrText,
              truncated: stdout.truncated || stderr.truncated,
              durationMs: Date.now() - start,
            };
            resolve({
              status: passed ? "ok" : "error",
              output,
              errorCode: passed ? undefined : code === null ? "signal" : `exit_${code}`,
              errorMessage: passed
                ? undefined
                : stderrText.slice(0, 500) || (code === null ? "terminated by signal" : `exit ${code}`),
              durationMs: Date.now() - start,
              metadata: {
                evidenceKind: "verification",
                semanticStatus: passed ? "success" : "failed",
              },
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
