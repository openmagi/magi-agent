import { createWriteStream, type WriteStream } from "node:fs";
import fs from "node:fs/promises";
import { finished } from "node:stream/promises";
import type { ToolContext } from "../Tool.js";
import type { Workspace } from "../storage/Workspace.js";

export interface CommandOutputLogFiles {
  stdoutFile?: string;
  stderrFile?: string;
}

export interface CommandOutputLogWriter {
  writable: WriteStream;
}

type StreamName = "stdout" | "stderr";

class CommandOutputLogStream {
  private closed = false;
  private failed = false;

  constructor(
    readonly relPath: string,
    private readonly fullPath: string,
    readonly writable: WriteStream,
  ) {
    this.writable.on("error", () => {
      this.failed = true;
    });
  }

  async close(): Promise<boolean> {
    if (!this.closed) {
      this.closed = true;
      this.writable.end();
      try {
        await finished(this.writable);
      } catch {
        this.failed = true;
      }
    }
    return !this.failed;
  }

  async remove(): Promise<void> {
    await fs.rm(this.fullPath, { force: true });
  }
}

export interface CommandOutputLogSink {
  stdout: CommandOutputLogWriter;
  stderr: CommandOutputLogWriter;
  stdoutFile: string;
  stderrFile: string;
  finalize(truncated: { stdout: boolean; stderr: boolean }): Promise<CommandOutputLogFiles>;
  discard(): Promise<void>;
}

let logSequence = 0;

function safeSegment(value: string): string {
  return value.replace(/[^a-zA-Z0-9_.-]/g, "-").replace(/^-+|-+$/g, "") || "unknown";
}

function nextStamp(): string {
  const sequence = logSequence;
  logSequence = (logSequence + 1) % 10_000;
  return `${Date.now()}${String(sequence).padStart(4, "0")}`;
}

function outputRelPath(input: {
  turnId: string;
  toolName: string;
  toolUseId?: string;
  stamp: string;
  stream: StreamName;
}): string {
  const safeTurn = safeSegment(input.turnId);
  const safeToolUse = safeSegment(input.toolUseId ?? input.toolName);
  return `.openmagi/command-logs/${safeTurn}/${safeToolUse}-${input.stamp}-${input.stream}.log`;
}

export async function createCommandOutputLogSink(
  workspace: Workspace,
  ctx: ToolContext,
  toolName: string,
): Promise<CommandOutputLogSink> {
  const stamp = nextStamp();
  const stdoutRel = outputRelPath({
    turnId: ctx.turnId,
    toolUseId: ctx.toolUseId,
    toolName,
    stamp,
    stream: "stdout",
  });
  const stderrRel = outputRelPath({
    turnId: ctx.turnId,
    toolUseId: ctx.toolUseId,
    toolName,
    stamp,
    stream: "stderr",
  });
  const stdoutFull = workspace.resolve(stdoutRel);
  const stderrFull = workspace.resolve(stderrRel);
  await fs.mkdir(workspace.resolve(`.openmagi/command-logs/${safeSegment(ctx.turnId)}`), {
    recursive: true,
  });

  const stdout = new CommandOutputLogStream(stdoutRel, stdoutFull, createWriteStream(stdoutFull));
  const stderr = new CommandOutputLogStream(stderrRel, stderrFull, createWriteStream(stderrFull));

  return {
    stdout,
    stderr,
    stdoutFile: stdout.relPath,
    stderrFile: stderr.relPath,
    async finalize(truncated): Promise<CommandOutputLogFiles> {
      const [stdoutOk, stderrOk] = await Promise.all([stdout.close(), stderr.close()]);
      const files: CommandOutputLogFiles = {};
      if (truncated.stdout && stdoutOk) {
        files.stdoutFile = stdout.relPath;
      } else {
        await stdout.remove();
      }
      if (truncated.stderr && stderrOk) {
        files.stderrFile = stderr.relPath;
      } else {
        await stderr.remove();
      }
      return files;
    },
    async discard(): Promise<void> {
      await Promise.all([stdout.close(), stderr.close()]);
      await Promise.all([stdout.remove(), stderr.remove()]);
    },
  };
}
