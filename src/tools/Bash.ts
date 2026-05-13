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
import type { BackgroundTaskRegistry } from "../tasks/BackgroundTaskRegistry.js";
import {
  createCommandOutputLogSink,
  type CommandOutputLogSink,
} from "../util/CommandOutputLog.js";
import { Utf8StreamCapture } from "../util/Utf8StreamCapture.js";
import { errorResult } from "../util/toolResult.js";
import { withMagiBinPath } from "../util/shellPath.js";
import {
  commandMayWriteProtectedMemory,
  commandMentionsProtectedMemory,
  isIncognitoMemoryMode,
  isLongTermMemoryWriteDisabled,
  protectedMemoryError,
} from "../util/memoryMode.js";

export interface BashInput {
  command: string;
  /** Timeout in ms; default 120_000, hard cap 600_000. */
  timeoutMs?: number;
  /** Workspace-relative cwd; default workspace root. */
  cwd?: string;
  /** Run command in background and return a task id immediately. */
  runInBackground?: boolean;
}

export interface BashOutput {
  exitCode: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
  /** Workspace-relative path to full stdout when the preview was truncated or the command is backgrounded. */
  stdoutFile?: string;
  /** Workspace-relative path to full stderr when the preview was truncated or the command is backgrounded. */
  stderrFile?: string;
  /** Present when the command was started as a background task. */
  backgroundTaskId?: string;
  background?: boolean;
  truncated: boolean;
  timedOut: boolean;
  durationMs: number;
}

export type BashSemanticStatus = "success" | "no_match" | "different" | "failed";

interface BashExitSemantics {
  status: "ok" | "error";
  semanticStatus: BashSemanticStatus;
  errorCode?: string;
  errorMessage?: string;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    command: { type: "string", description: "Shell command line. Executed via /bin/sh -c." },
    timeoutMs: { type: "integer", minimum: 100, description: "Timeout in ms (max 600000)." },
    cwd: { type: "string", description: "Workspace-relative working directory." },
    runInBackground: {
      type: "boolean",
      description:
        "Start the command as a background task and return immediately with backgroundTaskId. Use TaskGet/TaskOutput/TaskStop to inspect or stop it. Do not add '&' to the command.",
    },
  },
  required: ["command"],
} as const;

const MAX_OUTPUT_BYTES = 512 * 1024;
const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_TIMEOUT_MS = 600_000;
let backgroundSequence = 0;

function nextBackgroundTaskId(): string {
  const sequence = backgroundSequence;
  backgroundSequence = (backgroundSequence + 1) % 10_000;
  const rand = Math.random().toString(36).slice(2, 8);
  return `shell_${Date.now().toString(36)}_${sequence.toString(36)}_${rand}`;
}

function commandContainsExecutable(command: string, names: readonly string[]): boolean {
  const escaped = names.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(
    `(^|[\\s|;&()])(?:/?[A-Za-z0-9_.-]+/)*(${escaped.join("|")})(\\s|$)`,
  );
  return pattern.test(command);
}

export function interpretBashExit(
  command: string,
  code: number | null,
  stderrText: string,
): BashExitSemantics {
  if (code === 0) {
    return { status: "ok", semanticStatus: "success" };
  }
  if (code === 1 && commandContainsExecutable(command, ["grep", "egrep", "fgrep", "rg"])) {
    return { status: "ok", semanticStatus: "no_match" };
  }
  if (code === 1 && commandContainsExecutable(command, ["diff", "cmp"])) {
    return { status: "ok", semanticStatus: "different" };
  }
  const errorCode = code === null ? "signal" : `exit_${code}`;
  return {
    status: "error",
    semanticStatus: "failed",
    errorCode,
    errorMessage: stderrText.slice(0, 500) || (code === null ? "terminated by signal" : `exit ${code}`),
  };
}

export function makeBashTool(
  workspaceRoot: string,
  backgroundRegistry?: BackgroundTaskRegistry,
): Tool<BashInput, BashOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "Bash",
    description:
      "Run a shell command. cwd is the workspace root by default. Output is captured and truncated at ~512 KB per stream; truncated streams include stdoutFile/stderrFile paths to full logs. Set runInBackground=true for long-running commands you do not need immediately; then inspect with TaskGet/TaskOutput or stop with TaskStop. Prefer FileRead/FileWrite/FileEdit/Grep/Glob for file operations — this tool is for anything else.",
    inputSchema: INPUT_SCHEMA,
    permission: "execute",
    dangerous: true,
    validate(input) {
      if (!input || typeof input.command !== "string" || input.command.length === 0) {
        return "`command` is required";
      }
      return null;
    },
    async execute(input: BashInput, ctx: ToolContext): Promise<ToolResult<BashOutput>> {
      const start = Date.now();
      if (
        (isIncognitoMemoryMode(ctx.memoryMode) && commandMentionsProtectedMemory(input.command)) ||
        (isLongTermMemoryWriteDisabled(ctx.memoryMode) && commandMayWriteProtectedMemory(input.command))
      ) {
        return {
          status: "permission_denied",
          errorCode: "memory_access_blocked",
          errorMessage: protectedMemoryError("memory files in Bash command"),
          durationMs: Date.now() - start,
        };
      }
      const timeoutMs = Math.min(MAX_TIMEOUT_MS, input.timeoutMs ?? DEFAULT_TIMEOUT_MS);
      const ws = ctx.spawnWorkspace ?? defaultWorkspace;
      let cwd: string;
      try {
        cwd = input.cwd ? ws.resolve(input.cwd) : ws.root;
      } catch (err) {
        return errorResult(err, start);
      }
      let outputLogs: CommandOutputLogSink;
      try {
        outputLogs = await createCommandOutputLogSink(ws, ctx, "Bash");
      } catch (err) {
        return errorResult(err, start);
      }

      if (input.runInBackground === true) {
        if (!backgroundRegistry) {
          await outputLogs.discard();
          return {
            status: "error",
            errorCode: "background_unavailable",
            errorMessage: "background Bash requires a BackgroundTaskRegistry",
            durationMs: Date.now() - start,
          };
        }
        return startBackgroundBash({
          input,
          ctx,
          cwd,
          timeoutMs,
          start,
          outputLogs,
          registry: backgroundRegistry,
        });
      }

      return new Promise<ToolResult<BashOutput>>((resolve) => {
        let settled = false;
        const settle = (result: ToolResult<BashOutput>): void => {
          if (settled) return;
          settled = true;
          resolve(result);
        };
        try {
          const child = spawn("/bin/sh", ["-c", input.command], {
            cwd,
            env: { ...withMagiBinPath(process.env), PWD: cwd },
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

          ctx.abortSignal.addEventListener(
            "abort",
            () => child.kill("SIGTERM"),
            { once: true },
          );

          child.on("close", async (code, signal) => {
            clearTimeout(timeout);
            const stdoutText = stdout.end();
            const stderrText = stderr.end();
            const outputFiles = await outputLogs.finalize({
              stdout: stdout.truncated,
              stderr: stderr.truncated,
            });
            const semantics = timedOut
              ? {
                  status: "error" as const,
                  semanticStatus: "failed" as const,
                  errorCode: "timeout",
                  errorMessage: `command timed out after ${timeoutMs}ms`,
                }
              : interpretBashExit(input.command, code, stderrText);
            settle({
              status: semantics.status,
              output: {
                exitCode: code,
                signal,
                stdout: stdoutText,
                stderr: stderrText,
                ...outputFiles,
                truncated: stdout.truncated || stderr.truncated,
                timedOut,
                durationMs: Date.now() - start,
              },
              errorCode: semantics.errorCode,
              errorMessage: semantics.errorMessage,
              durationMs: Date.now() - start,
              metadata: {
                semanticStatus: semantics.semanticStatus,
                ...(timedOut ? { timedOut: true, timeoutMs } : {}),
              },
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

async function startBackgroundBash(input: {
  input: BashInput;
  ctx: ToolContext;
  cwd: string;
  timeoutMs: number;
  start: number;
  outputLogs: CommandOutputLogSink;
  registry: BackgroundTaskRegistry;
}): Promise<ToolResult<BashOutput>> {
  const taskId = nextBackgroundTaskId();
  const controller = new AbortController();
  try {
    await input.registry.create({
      taskId,
      parentTurnId: input.ctx.turnId,
      sessionKey: input.ctx.sessionKey,
      persona: "bash",
      prompt: input.input.command,
      abortController: controller,
    });
  } catch (err) {
    await input.outputLogs.discard();
    return errorResult(err, input.start);
  }

  runBackgroundBash({ ...input, taskId, controller });

  return {
    status: "ok",
    output: {
      exitCode: null,
      signal: null,
      stdout: "",
      stderr: "",
      stdoutFile: input.outputLogs.stdoutFile,
      stderrFile: input.outputLogs.stderrFile,
      backgroundTaskId: taskId,
      background: true,
      truncated: false,
      timedOut: false,
      durationMs: Date.now() - input.start,
    },
    durationMs: Date.now() - input.start,
    metadata: { background: true, taskId },
  };
}

function runBackgroundBash(input: {
  input: BashInput;
  ctx: ToolContext;
  cwd: string;
  timeoutMs: number;
  start: number;
  outputLogs: CommandOutputLogSink;
  registry: BackgroundTaskRegistry;
  taskId: string;
  controller: AbortController;
}): void {
  const stdout = new Utf8StreamCapture(MAX_OUTPUT_BYTES);
  const stderr = new Utf8StreamCapture(MAX_OUTPUT_BYTES);
  let timedOut = false;
  let finished = false;
  let timeout: NodeJS.Timeout | undefined;

  const finish = async (status: "completed" | "failed", output: BashOutput, error?: string): Promise<void> => {
    if (finished) return;
    finished = true;
    if (timeout) clearTimeout(timeout);
    const current = await input.registry.get(input.taskId);
    if (current?.status !== "running") return;
    await input.registry.attachResult(input.taskId, {
      status,
      resultText: JSON.stringify(output),
      ...(error ? { error } : {}),
    });
    input.ctx.emitAgentEvent?.({
      type: "background_task",
      taskId: input.taskId,
      persona: "bash",
      status,
      ...(error ? { detail: error } : { detail: "command completed" }),
    });
  };

  try {
    const child = spawn("/bin/sh", ["-c", input.input.command], {
      cwd: input.cwd,
      env: { ...withMagiBinPath(process.env), PWD: input.cwd },
      stdio: ["ignore", "pipe", "pipe"],
    });
    child.stdout.pipe(input.outputLogs.stdout.writable, { end: false });
    child.stderr.pipe(input.outputLogs.stderr.writable, { end: false });
    child.stdout.on("data", (c: Buffer) => {
      stdout.write(c);
      const text = c.toString("utf8").trim();
      if (text) {
        void input.registry.recordProgress(input.taskId, `stdout: ${text.slice(-200)}`);
      }
    });
    child.stderr.on("data", (c: Buffer) => {
      stderr.write(c);
      const text = c.toString("utf8").trim();
      if (text) {
        void input.registry.recordProgress(input.taskId, `stderr: ${text.slice(-200)}`);
      }
    });
    controllerAbortKill(input.controller, child);
    timeout = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 3_000).unref();
    }, input.timeoutMs);

    child.on("close", (code, signal) => {
      void (async () => {
        const stdoutText = stdout.end();
        const stderrText = stderr.end();
        const outputFiles = await input.outputLogs.finalize({
          stdout: true,
          stderr: true,
        });
        const output: BashOutput = {
          exitCode: code,
          signal,
          stdout: stdoutText,
          stderr: stderrText,
          ...outputFiles,
          backgroundTaskId: input.taskId,
          background: true,
          truncated: stdout.truncated || stderr.truncated,
          timedOut,
          durationMs: Date.now() - input.start,
        };
        const semantics = timedOut
          ? {
              status: "error" as const,
              errorCode: "timeout",
              errorMessage: `command timed out after ${input.timeoutMs}ms`,
            }
          : interpretBashExit(input.input.command, code, stderrText);
        await finish(
          semantics.status === "ok" ? "completed" : "failed",
          output,
          semantics.errorMessage,
        );
      })().catch(async (err: unknown) => {
        await input.registry
          .attachResult(input.taskId, {
            status: "failed",
            error: err instanceof Error ? err.message : String(err),
          })
          .catch(() => {});
      });
    });
    child.on("error", (err) => {
      void input.outputLogs.discard().finally(() => {
        void finish(
          "failed",
          {
            exitCode: null,
            signal: null,
            stdout: "",
            stderr: "",
            backgroundTaskId: input.taskId,
            background: true,
            truncated: false,
            timedOut,
            durationMs: Date.now() - input.start,
          },
          err.message,
        );
      });
    });
  } catch (err) {
    void input.outputLogs.discard().finally(() => {
      void finish(
        "failed",
        {
          exitCode: null,
          signal: null,
          stdout: "",
          stderr: "",
          backgroundTaskId: input.taskId,
          background: true,
          truncated: false,
          timedOut,
          durationMs: Date.now() - input.start,
        },
        err instanceof Error ? err.message : String(err),
      );
    });
  }
}

function controllerAbortKill(
  controller: AbortController,
  child: ReturnType<typeof spawn>,
): void {
  controller.signal.addEventListener(
    "abort",
    () => {
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 3_000).unref();
    },
    { once: true },
  );
}
