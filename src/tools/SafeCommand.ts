import { spawn } from "node:child_process";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import {
  createCommandOutputLogSink,
  type CommandOutputLogSink,
} from "../util/CommandOutputLog.js";
import { Utf8StreamCapture } from "../util/Utf8StreamCapture.js";
import { errorResult } from "../util/toolResult.js";
import { withMagiBinPath } from "../util/shellPath.js";

export interface SafeCommandInput {
  command: string;
  args?: string[];
  /** Timeout in ms; default 120_000, hard cap 600_000. */
  timeoutMs?: number;
  /** Workspace-relative cwd; default workspace root. */
  cwd?: string;
}

export interface SafeCommandOutput {
  command: string;
  args: string[];
  cwd: string;
  exitCode: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
  /** Workspace-relative path to full stdout when the stdout preview was truncated. */
  stdoutFile?: string;
  /** Workspace-relative path to full stderr when the stderr preview was truncated. */
  stderrFile?: string;
  truncated: boolean;
  timedOut: boolean;
  durationMs: number;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    command: {
      type: "string",
      description:
        "Allowlisted command to run without a shell. Allowed: git, rg, grep, ls, cat, head, tail, wc, file, printf, env.",
    },
    args: {
      type: "array",
      items: { type: "string" },
      description: "Argument vector passed directly to the command. Shell syntax is not interpreted.",
    },
    timeoutMs: { type: "integer", minimum: 100, description: "Timeout in ms (max 600000)." },
    cwd: { type: "string", description: "Workspace-relative working directory." },
  },
  required: ["command"],
} as const;

const MAX_OUTPUT_BYTES = 512 * 1024;
const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_TIMEOUT_MS = 600_000;
const MAX_ARGS = 100;
const MAX_ARG_BYTES = 8192;

const ALLOWED_COMMANDS = new Set([
  "cat",
  "env",
  "file",
  "git",
  "grep",
  "head",
  "ls",
  "printf",
  "rg",
  "tail",
  "wc",
]);

const ALLOWED_GIT_SUBCOMMANDS = new Set([
  "diff",
  "grep",
  "log",
  "ls-files",
  "rev-parse",
  "show",
  "status",
]);

const DENIED_GIT_OPTIONS = new Set(["-C", "-c", "--exec-path", "--git-dir", "--work-tree"]);

interface Denial {
  errorCode: string;
  errorMessage: string;
}

function permissionDenied(denial: Denial, start: number): ToolResult<SafeCommandOutput> {
  return {
    status: "permission_denied",
    errorCode: denial.errorCode,
    errorMessage: denial.errorMessage,
    durationMs: Date.now() - start,
  };
}

function isUnsafePathLikeArg(arg: string): boolean {
  if (arg.includes("\0")) return true;
  if (arg.startsWith("/") || arg.startsWith("~")) return true;
  if (arg === ".." || arg.startsWith("../")) return true;
  if (arg.includes("/../") || arg.endsWith("/..")) return true;
  if (/=(?:\/|~|\.\.(?:\/|$))/.test(arg)) return true;
  const normalized = arg.replace(/^\.\//, "");
  if (normalized === ".env" || normalized.startsWith(".env.")) return true;
  if (normalized === ".ssh" || normalized.startsWith(".ssh/")) return true;
  if (normalized === "secrets" || normalized.startsWith("secrets/")) return true;
  if (
    normalized.includes("/.env") ||
    normalized.includes("/.ssh/") ||
    normalized.includes("/secrets/")
  ) {
    return true;
  }
  return false;
}

function inspectGitArgs(args: readonly string[]): Denial | null {
  for (let i = 0; i < args.length; i++) {
    const arg = args[i] ?? "";
    if (DENIED_GIT_OPTIONS.has(arg)) {
      return {
        errorCode: "unsafe_argument",
        errorMessage: `git option is not allowed: ${arg}`,
      };
    }
    if (
      arg.startsWith("--git-dir=") ||
      arg.startsWith("--work-tree=") ||
      arg.startsWith("--exec-path=") ||
      arg.startsWith("-c")
    ) {
      return {
        errorCode: "unsafe_argument",
        errorMessage: `git option is not allowed: ${arg}`,
      };
    }
  }

  const subcommand = args.find((arg) => arg.length > 0 && !arg.startsWith("-"));
  if (!subcommand || !ALLOWED_GIT_SUBCOMMANDS.has(subcommand)) {
    return {
      errorCode: "git_subcommand_not_allowed",
      errorMessage: `git subcommand is not allowed: ${subcommand ?? "(none)"}`,
    };
  }
  return null;
}

function inspectInput(input: SafeCommandInput): Denial | null {
  if (!input || typeof input.command !== "string" || input.command.length === 0) {
    return { errorCode: "invalid_input", errorMessage: "`command` is required" };
  }
  if (input.command.includes("/") || input.command.includes("\\")) {
    return {
      errorCode: "command_not_allowed",
      errorMessage: "`command` must be an allowlisted command name, not a path",
    };
  }
  if (!ALLOWED_COMMANDS.has(input.command)) {
    return {
      errorCode: "command_not_allowed",
      errorMessage: `command is not allowlisted: ${input.command}`,
    };
  }

  const args = input.args ?? [];
  if (!Array.isArray(args)) {
    return { errorCode: "invalid_input", errorMessage: "`args` must be an array of strings" };
  }
  if (args.length > MAX_ARGS) {
    return { errorCode: "too_many_args", errorMessage: `args length exceeds ${MAX_ARGS}` };
  }
  for (const arg of args) {
    if (typeof arg !== "string") {
      return { errorCode: "invalid_input", errorMessage: "`args` must be an array of strings" };
    }
    if (Buffer.byteLength(arg, "utf8") > MAX_ARG_BYTES) {
      return { errorCode: "argument_too_large", errorMessage: `argument exceeds ${MAX_ARG_BYTES} bytes` };
    }
    if (isUnsafePathLikeArg(arg)) {
      return {
        errorCode: "unsafe_argument",
        errorMessage: `argument is outside the workspace boundary: ${arg}`,
      };
    }
  }

  if (input.command === "env" && args.length > 0) {
    return {
      errorCode: "unsafe_argument",
      errorMessage: "`env` is only allowed without arguments",
    };
  }
  if (input.command === "git") {
    return inspectGitArgs(args);
  }
  return null;
}

function safeEnv(cwd: string, workspaceRoot: string): NodeJS.ProcessEnv {
  const source = withMagiBinPath(process.env);
  const env: NodeJS.ProcessEnv = {
    HOME: workspaceRoot,
    PATH: source.PATH ?? "",
    PWD: cwd,
  };
  for (const key of ["LANG", "LC_ALL", "TZ"] as const) {
    const value = source[key];
    if (value) env[key] = value;
  }
  return env;
}

export function makeSafeCommandTool(workspaceRoot: string): Tool<SafeCommandInput, SafeCommandOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "SafeCommand",
    description:
      "Run an allowlisted command without shell interpretation. Use this for safe workspace inspection when Bash is unnecessary. Truncated streams include stdoutFile/stderrFile paths to full logs. Allowed commands include rg, grep, ls, cat, head, tail, wc, file, printf, env, and read-only git subcommands.",
    inputSchema: INPUT_SCHEMA,
    permission: "execute",
    dangerous: false,
    mutatesWorkspace: false,
    isConcurrencySafe: false,
    validate(input) {
      return inspectInput(input as SafeCommandInput)?.errorMessage ?? null;
    },
    async execute(input: SafeCommandInput, ctx: ToolContext): Promise<ToolResult<SafeCommandOutput>> {
      const start = Date.now();
      const denial = inspectInput(input);
      if (denial) return permissionDenied(denial, start);

      const timeoutMs = Math.min(MAX_TIMEOUT_MS, input.timeoutMs ?? DEFAULT_TIMEOUT_MS);
      const args = input.args ?? [];
      const ws = ctx.spawnWorkspace ?? defaultWorkspace;
      let cwd: string;
      try {
        cwd = input.cwd ? ws.resolve(input.cwd) : ws.root;
      } catch (err) {
        return errorResult(err, start);
      }
      let outputLogs: CommandOutputLogSink;
      try {
        outputLogs = await createCommandOutputLogSink(ws, ctx, "SafeCommand");
      } catch (err) {
        return errorResult(err, start);
      }

      return new Promise<ToolResult<SafeCommandOutput>>((resolve) => {
        let settled = false;
        const settle = (result: ToolResult<SafeCommandOutput>): void => {
          if (settled) return;
          settled = true;
          resolve(result);
        };
        try {
          const child = spawn(input.command, args, {
            cwd,
            env: safeEnv(cwd, ws.root),
            stdio: ["ignore", "pipe", "pipe"],
          });
          const stdout = new Utf8StreamCapture(MAX_OUTPUT_BYTES);
          const stderr = new Utf8StreamCapture(MAX_OUTPUT_BYTES);
          child.stdout.pipe(outputLogs.stdout.writable, { end: false });
          child.stderr.pipe(outputLogs.stderr.writable, { end: false });
          child.stdout.on("data", (c: Buffer) => stdout.write(c));
          child.stderr.on("data", (c: Buffer) => stderr.write(c));

          let timedOut = false;
          const timeout = setTimeout(() => {
            timedOut = true;
            child.kill("SIGTERM");
            setTimeout(() => child.kill("SIGKILL"), 3_000).unref();
          }, timeoutMs);
          ctx.abortSignal.addEventListener("abort", () => child.kill("SIGTERM"), {
            once: true,
          });

          child.on("close", async (code, signal) => {
            clearTimeout(timeout);
            const stdoutText = stdout.end();
            const stderrText = stderr.end();
            const durationMs = Date.now() - start;
            const outputFiles = await outputLogs.finalize({
              stdout: stdout.truncated,
              stderr: stderr.truncated,
            });
            const output: SafeCommandOutput = {
              command: input.command,
              args,
              cwd,
              exitCode: code,
              signal,
              stdout: stdoutText,
              stderr: stderrText,
              ...outputFiles,
              truncated: stdout.truncated || stderr.truncated,
              timedOut,
              durationMs,
            };
            const status = code === 0 && !timedOut ? "ok" : "error";
            const errorCode = timedOut
              ? "timeout"
              : code === 0 ? undefined : code === null ? "signal" : `exit_${code}`;
            const errorMessage = timedOut
              ? `command timed out after ${timeoutMs}ms`
              : code === 0
                ? undefined
                : stderrText.slice(0, 500) || (code === null ? "terminated by signal" : `exit ${code}`);
            settle({
              status,
              output,
              errorCode,
              errorMessage,
              durationMs,
              ...(timedOut ? { metadata: { timedOut: true, timeoutMs } } : {}),
            });
          });
          child.on("error", async (err) => {
            clearTimeout(timeout);
            await outputLogs.discard();
            settle(errorResult(err, start));
          });
        } catch (err) {
          void outputLogs.discard().finally(() => {
            settle(errorResult(err, start));
          });
        }
      });
    },
  };
}
