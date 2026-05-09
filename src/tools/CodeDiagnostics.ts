import fs from "node:fs/promises";
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";

const execFileAsync = promisify(execFile);

export interface CodeDiagnosticsInput {
  projectPath?: string;
  maxDiagnostics?: number;
}

export interface CodeDiagnostic {
  file: string;
  line: number;
  column: number;
  severity: "error" | "warning";
  code: string;
  message: string;
}

export interface CodeDiagnosticsOutput {
  cwd: string;
  checker: "typescript";
  passed: boolean;
  exitCode: number;
  diagnosticCount: number;
  diagnostics: CodeDiagnostic[];
  raw: string;
  truncated: boolean;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    projectPath: {
      type: "string",
      description: "Workspace-relative project directory. Default: workspace root.",
    },
    maxDiagnostics: {
      type: "integer",
      minimum: 1,
      maximum: 200,
      description: "Maximum diagnostics to return. Default: 50.",
    },
  },
} as const;

export function makeCodeDiagnosticsTool(
  workspaceRoot: string,
): Tool<CodeDiagnosticsInput, CodeDiagnosticsOutput> {
  const workspace = new Workspace(workspaceRoot);
  return {
    name: "CodeDiagnostics",
    description:
      "Run deterministic code diagnostics for a project. For TypeScript projects, runs tsc --noEmit --pretty false and returns structured file/line/type errors.",
    inputSchema: INPUT_SCHEMA,
    permission: "execute",
    kind: "core",
    mutatesWorkspace: false,
    validate(input) {
      if (!input) return null;
      if (
        input.projectPath !== undefined &&
        typeof input.projectPath !== "string"
      ) {
        return "`projectPath` must be a string";
      }
      if (
        input.maxDiagnostics !== undefined &&
        (!Number.isInteger(input.maxDiagnostics) ||
          input.maxDiagnostics < 1 ||
          input.maxDiagnostics > 200)
      ) {
        return "`maxDiagnostics` must be an integer in [1..200]";
      }
      return null;
    },
    async execute(
      input: CodeDiagnosticsInput,
      _ctx: ToolContext,
    ): Promise<ToolResult<CodeDiagnosticsOutput>> {
      const start = Date.now();
      try {
        const cwd = workspace.resolve(input.projectPath ?? ".");
        const tsconfig = path.join(cwd, "tsconfig.json");
        try {
          await fs.access(tsconfig);
        } catch {
          return {
            status: "error",
            errorCode: "no_tsconfig",
            errorMessage: `No tsconfig.json found at ${relativeToWorkspace(workspaceRoot, tsconfig)}`,
            durationMs: Date.now() - start,
          };
        }

        const tsc = await resolveTscBinary();
        const { exitCode, combined } = await runTsc(tsc, cwd);
        const maxDiagnostics = input.maxDiagnostics ?? 50;
        const diagnostics = parseTypeScriptDiagnostics(combined, cwd).slice(
          0,
          maxDiagnostics,
        );
        const output: CodeDiagnosticsOutput = {
          cwd: relativeToWorkspace(workspaceRoot, cwd),
          checker: "typescript",
          passed: exitCode === 0,
          exitCode,
          diagnosticCount: diagnostics.length,
          diagnostics,
          raw: combined.slice(0, 64 * 1024),
          truncated: combined.length > 64 * 1024,
        };
        return {
          status: "ok",
          output,
          metadata: {
            evidenceKind: "diagnostics",
            checker: "typescript",
            passed: output.passed,
            diagnosticCount: output.diagnosticCount,
            diagnostics: output.diagnostics,
          },
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return {
          status: "error",
          errorCode: "diagnostics_failed",
          errorMessage: err instanceof Error ? err.message : String(err),
          durationMs: Date.now() - start,
        };
      }
    },
  };
}

async function resolveTscBinary(): Promise<string> {
  const candidates = [
    process.env.TSC_BIN,
    path.join(process.cwd(), "node_modules", ".bin", "tsc"),
    "tsc",
  ].filter((candidate): candidate is string => Boolean(candidate));
  for (const candidate of candidates) {
    if (candidate === "tsc") return candidate;
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      /* try next */
    }
  }
  return "tsc";
}

async function runTsc(
  tsc: string,
  cwd: string,
): Promise<{ exitCode: number; combined: string }> {
  try {
    const { stdout, stderr } = await execFileAsync(
      tsc,
      ["--noEmit", "--pretty", "false"],
      {
        cwd,
        timeout: 120_000,
        maxBuffer: 10 * 1024 * 1024,
      },
    );
    return { exitCode: 0, combined: `${stdout}${stderr}` };
  } catch (err) {
    const e = err as NodeJS.ErrnoException & {
      stdout?: string;
      stderr?: string;
      code?: number | string;
    };
    const code = typeof e.code === "number" ? e.code : 1;
    return { exitCode: code, combined: `${e.stdout ?? ""}${e.stderr ?? ""}` };
  }
}

function parseTypeScriptDiagnostics(raw: string, cwd: string): CodeDiagnostic[] {
  const diagnostics: CodeDiagnostic[] = [];
  const cwdResolved = path.resolve(cwd);
  const re = /^(.+?)\((\d+),(\d+)\):\s+(error|warning)\s+(TS\d+):\s+(.+)$/;
  for (const line of raw.split(/\r?\n/)) {
    const match = re.exec(line.trim());
    if (!match) continue;
    const file = path.resolve(cwdResolved, match[1]!);
    diagnostics.push({
      file: relativeToWorkspace(cwdResolved, file),
      line: Number.parseInt(match[2]!, 10),
      column: Number.parseInt(match[3]!, 10),
      severity: match[4] as "error" | "warning",
      code: match[5]!,
      message: match[6]!,
    });
  }
  return diagnostics;
}

function relativeToWorkspace(root: string, target: string): string {
  const rel = path.relative(path.resolve(root), path.resolve(target));
  return rel.length === 0 ? "." : rel.split(path.sep).join("/");
}
