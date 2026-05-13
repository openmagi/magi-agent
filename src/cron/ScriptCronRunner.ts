import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { Utf8StreamCapture } from "../util/Utf8StreamCapture.js";

export interface ScriptCronResult {
  code: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
}

function assertInsideWorkspace(root: string, target: string): void {
  const relative = path.relative(root, target);
  if (relative.startsWith("..") || path.isAbsolute(relative) || relative.length === 0) {
    throw new Error("script path outside workspace");
  }
}

export async function runScriptCron(input: {
  workspaceRoot: string;
  scriptPath: string;
  timeoutMs: number;
}): Promise<ScriptCronResult> {
  const root = await fs.realpath(input.workspaceRoot);
  const resolved = path.resolve(root, input.scriptPath);
  assertInsideWorkspace(root, resolved);
  const target = await fs.realpath(resolved);
  assertInsideWorkspace(root, target);

  return new Promise((resolve, reject) => {
    const stdout = new Utf8StreamCapture(64 * 1024);
    const stderr = new Utf8StreamCapture(64 * 1024);
    const child = spawn("/bin/sh", [target], {
      cwd: root,
      env: {
        PATH: process.env.PATH ?? "/usr/bin:/bin",
        HOME: root,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let settled = false;
    let timedOut = false;
    const finish = (result: ScriptCronResult): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(result);
    };
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
    }, Math.max(1, input.timeoutMs));

    child.stdout?.on("data", (chunk) => stdout.write(chunk));
    child.stderr?.on("data", (chunk) => stderr.write(chunk));
    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(err);
    });
    child.on("close", (code) => {
      finish({
        code,
        stdout: stdout.end(),
        stderr: stderr.end(),
        timedOut,
      });
    });
  });
}
