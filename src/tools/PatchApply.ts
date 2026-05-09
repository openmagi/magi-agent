import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";
import { isFsSafeEscape, readSafe, statSafe, writeSafe } from "../util/fsSafe.js";
import {
  isLongTermMemoryWriteDisabled,
  isProtectedMemoryPath,
  protectedMemoryError,
} from "../util/memoryMode.js";

export interface PatchApplyInput {
  patch: string;
  dry_run?: boolean;
}

export interface PatchApplyFileOutput {
  path: string;
  operation: "create" | "update" | "delete";
  hunks: number;
  addedLines: number;
  removedLines: number;
  oldSha256: string;
  newSha256: string;
}

export interface PatchApplyOutput {
  dryRun: boolean;
  changedFiles: string[];
  createdFiles: string[];
  deletedFiles: string[];
  files: PatchApplyFileOutput[];
}

export interface PatchApplyApprovalInput {
  toolName: "PatchApply";
  patchPreview?: PatchApplyOutput;
  previewError?: string;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    patch: {
      type: "string",
      description:
        "Unified diff to apply. Supports multi-file create/update/delete patches with ---/+++ and @@ hunks.",
    },
    dry_run: {
      type: "boolean",
      description: "Validate and summarize the patch without writing files.",
    },
  },
  required: ["patch"],
} as const;

interface ParsedHunkLine {
  kind: "context" | "remove" | "add";
  text: string;
}

interface ParsedHunk {
  oldStart: number;
  oldCount: number;
  newStart: number;
  newCount: number;
  lines: ParsedHunkLine[];
}

interface ParsedFilePatch {
  oldPath: string | null;
  newPath: string | null;
  hunks: ParsedHunk[];
}

interface PlannedFilePatch {
  path: string;
  operation: "create" | "update" | "delete";
  before: string;
  after: string;
  hunks: number;
  addedLines: number;
  removedLines: number;
}

class PatchApplyError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "PatchApplyError";
  }
}

function sha256(value: string): string {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function parseCount(value: string | undefined): number {
  return value === undefined ? 1 : Number.parseInt(value, 10);
}

function parseHeaderPath(line: string, marker: "---" | "+++"): string | null {
  const raw = line.slice(marker.length).trim().split(/\s+/)[0] ?? "";
  if (raw === "/dev/null") return null;
  if (raw.length === 0) {
    throw new PatchApplyError("invalid_patch", `missing path in ${marker} header`);
  }
  if (path.isAbsolute(raw)) {
    throw new PatchApplyError("path_escape", `patch path must be workspace-relative: ${raw}`);
  }
  return raw.replace(/^[ab]\//, "");
}

function parseUnifiedDiff(patch: string): ParsedFilePatch[] {
  const lines = patch.replace(/\r\n/g, "\n").split("\n");
  const files: ParsedFilePatch[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i] ?? "";
    if (!line.startsWith("--- ")) {
      i += 1;
      continue;
    }

    const oldPath = parseHeaderPath(line, "---");
    i += 1;
    const next = lines[i] ?? "";
    if (!next.startsWith("+++ ")) {
      throw new PatchApplyError("invalid_patch", `expected +++ header after ${line}`);
    }
    const newPath = parseHeaderPath(next, "+++");
    i += 1;

    const hunks: ParsedHunk[] = [];
    while (i < lines.length) {
      const header = lines[i] ?? "";
      if (header.startsWith("--- ")) break;
      if (header.trim().length === 0) {
        i += 1;
        continue;
      }
      const match = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(header);
      if (!match) {
        throw new PatchApplyError("invalid_patch", `expected hunk header, got: ${header}`);
      }
      const oldStart = Number.parseInt(match[1] ?? "0", 10);
      const oldCount = parseCount(match[2]);
      const newStart = Number.parseInt(match[3] ?? "0", 10);
      const newCount = parseCount(match[4]);
      i += 1;

      const hunkLines: ParsedHunkLine[] = [];
      let oldSeen = 0;
      let newSeen = 0;
      while (i < lines.length && (oldSeen < oldCount || newSeen < newCount)) {
        const hunkLine = lines[i] ?? "";
        if (hunkLine.startsWith("\\ No newline at end of file")) {
          i += 1;
          continue;
        }
        const prefix = hunkLine[0];
        const text = hunkLine.slice(1);
        if (prefix === " ") {
          hunkLines.push({ kind: "context", text });
          oldSeen += 1;
          newSeen += 1;
        } else if (prefix === "-") {
          hunkLines.push({ kind: "remove", text });
          oldSeen += 1;
        } else if (prefix === "+") {
          hunkLines.push({ kind: "add", text });
          newSeen += 1;
        } else {
          throw new PatchApplyError("invalid_patch", `invalid hunk line: ${hunkLine}`);
        }
        i += 1;
      }

      if (oldSeen !== oldCount || newSeen !== newCount) {
        throw new PatchApplyError(
          "invalid_patch",
          `hunk line counts do not match header ${header}`,
        );
      }
      hunks.push({ oldStart, oldCount, newStart, newCount, lines: hunkLines });
    }

    if (hunks.length === 0) {
      throw new PatchApplyError("invalid_patch", "file patch must contain at least one hunk");
    }
    files.push({ oldPath, newPath, hunks });
  }

  if (files.length === 0) {
    throw new PatchApplyError("invalid_patch", "patch contains no file hunks");
  }
  return files;
}

function splitFile(text: string): { lines: string[]; finalNewline: boolean } {
  if (text.length === 0) return { lines: [], finalNewline: false };
  const lines = text.split("\n");
  const finalNewline = lines.at(-1) === "";
  if (finalNewline) lines.pop();
  return { lines, finalNewline };
}

function joinFile(lines: string[], finalNewline: boolean): string {
  const body = lines.join("\n");
  return finalNewline && lines.length > 0 ? `${body}\n` : body;
}

function arraysEqual(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

function applyHunks(filePath: string, content: string, hunks: ParsedHunk[]): string {
  const split = splitFile(content);
  const lines = split.lines.slice();
  let offset = 0;

  for (const hunk of hunks) {
    const index = Math.max(0, hunk.oldStart - 1 + offset);
    const expected = hunk.lines
      .filter((line) => line.kind === "context" || line.kind === "remove")
      .map((line) => line.text);
    const replacement = hunk.lines
      .filter((line) => line.kind === "context" || line.kind === "add")
      .map((line) => line.text);
    const actual = lines.slice(index, index + expected.length);
    if (!arraysEqual(actual, expected)) {
      throw new PatchApplyError(
        "hunk_mismatch",
        `hunk context mismatch in ${filePath} at old line ${hunk.oldStart}`,
      );
    }
    lines.splice(index, expected.length, ...replacement);
    offset += replacement.length - expected.length;
  }

  return joinFile(lines, split.finalNewline);
}

function operationFor(file: ParsedFilePatch): "create" | "update" | "delete" {
  if (file.oldPath === null && file.newPath !== null) return "create";
  if (file.newPath === null && file.oldPath !== null) return "delete";
  return "update";
}

function targetPath(file: ParsedFilePatch): string {
  const target = file.newPath ?? file.oldPath;
  if (!target) {
    throw new PatchApplyError("invalid_patch", "file patch cannot delete /dev/null");
  }
  return target;
}

function countChangedLines(hunks: ParsedHunk[]): { addedLines: number; removedLines: number } {
  let addedLines = 0;
  let removedLines = 0;
  for (const hunk of hunks) {
    for (const line of hunk.lines) {
      if (line.kind === "add") addedLines += 1;
      if (line.kind === "remove") removedLines += 1;
    }
  }
  return { addedLines, removedLines };
}

async function planPatch(
  parsed: ParsedFilePatch[],
  ws: Workspace,
): Promise<PlannedFilePatch[]> {
  const planned: PlannedFilePatch[] = [];
  for (const file of parsed) {
    const operation = operationFor(file);
    const relPath = targetPath(file);
    // Resolve once during preflight so all path errors happen before writes.
    ws.resolve(relPath);

    const stat = await statSafe(relPath, ws.root);
    if (operation === "create") {
      if (stat !== null) {
        throw new PatchApplyError("file_exists", `${relPath} already exists`);
      }
      const after = applyHunks(relPath, "", file.hunks);
      planned.push({
        path: relPath,
        operation,
        before: "",
        after,
        hunks: file.hunks.length,
        ...countChangedLines(file.hunks),
      });
      continue;
    }

    if (stat === null) {
      throw new PatchApplyError("not_found", `${relPath} not found`);
    }
    if (!stat.isFile()) {
      throw new PatchApplyError("not_a_file", `${relPath} is not a regular file`);
    }

    const before = await readSafe(relPath, ws.root);
    const after = applyHunks(relPath, before, file.hunks);
    planned.push({
      path: relPath,
      operation,
      before,
      after,
      hunks: file.hunks.length,
      ...countChangedLines(file.hunks),
    });
  }
  return planned;
}

async function writePlan(planned: PlannedFilePatch[], ws: Workspace): Promise<void> {
  for (const file of planned) {
    if (file.operation === "delete") {
      await fs.rm(ws.resolve(file.path));
      continue;
    }
    await fs.mkdir(path.dirname(ws.resolve(file.path)), { recursive: true });
    await writeSafe(file.path, file.after, ws.root);
  }
}

function outputFor(planned: PlannedFilePatch[], dryRun: boolean): PatchApplyOutput {
  const files = planned.map((file): PatchApplyFileOutput => ({
    path: file.path,
    operation: file.operation,
    hunks: file.hunks,
    addedLines: file.addedLines,
    removedLines: file.removedLines,
    oldSha256: sha256(file.before),
    newSha256: sha256(file.after),
  }));
  return {
    dryRun,
    changedFiles: files.map((file) => file.path),
    createdFiles: files
      .filter((file) => file.operation === "create")
      .map((file) => file.path),
    deletedFiles: files
      .filter((file) => file.operation === "delete")
      .map((file) => file.path),
    files,
  };
}

export async function buildPatchApplyApprovalInput(
  input: unknown,
  workspaceRoot: string,
): Promise<PatchApplyApprovalInput> {
  if (!input || typeof input !== "object" || typeof (input as PatchApplyInput).patch !== "string") {
    return { toolName: "PatchApply", previewError: "invalid_input" };
  }

  try {
    const ws = new Workspace(workspaceRoot);
    const parsed = parseUnifiedDiff((input as PatchApplyInput).patch);
    const planned = await planPatch(parsed, ws);
    return {
      toolName: "PatchApply",
      patchPreview: outputFor(planned, (input as PatchApplyInput).dry_run === true),
    };
  } catch (err) {
    if (err instanceof PatchApplyError) {
      return { toolName: "PatchApply", previewError: err.code };
    }
    if (isFsSafeEscape(err)) {
      return { toolName: "PatchApply", previewError: "path_escape" };
    }
    return { toolName: "PatchApply", previewError: "preview_failed" };
  }
}

function emitPatchPreview(ctx: ToolContext, output: PatchApplyOutput): void {
  try {
    ctx.emitAgentEvent?.({
      type: "patch_preview",
      ...(ctx.toolUseId ? { toolUseId: ctx.toolUseId } : {}),
      dryRun: output.dryRun,
      changedFiles: output.changedFiles,
      createdFiles: output.createdFiles,
      deletedFiles: output.deletedFiles,
      files: output.files,
    });
  } catch {
    // Preview telemetry must never decide whether a validated patch writes.
  }
}

export function makePatchApplyTool(
  workspaceRoot: string,
): Tool<PatchApplyInput, PatchApplyOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "PatchApply",
    description:
      "Apply a multi-file unified diff safely inside the workspace. Use this for precise code changes when FileEdit would require many exact replacements. Set dry_run=true to validate and preview without writing.",
    inputSchema: INPUT_SCHEMA,
    permission: "write",
    mutatesWorkspace: true,
    isConcurrencySafe: false,
    validate(input) {
      if (!input || typeof input.patch !== "string" || input.patch.trim().length === 0) {
        return "`patch` is required";
      }
      return null;
    },
    async execute(input: PatchApplyInput, ctx: ToolContext): Promise<ToolResult<PatchApplyOutput>> {
      const start = Date.now();
      try {
        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        const parsed = parseUnifiedDiff(input.patch);
        for (const file of parsed) {
          const relPath = targetPath(file);
          if (isLongTermMemoryWriteDisabled(ctx.memoryMode) && isProtectedMemoryPath(relPath)) {
            return {
              status: "permission_denied",
              errorCode: "memory_write_blocked",
              errorMessage: protectedMemoryError(relPath),
              durationMs: Date.now() - start,
            };
          }
        }
        const planned = await planPatch(parsed, ws);
        const dryRun = input.dry_run === true;
        const output = outputFor(planned, dryRun);
        emitPatchPreview(ctx, output);
        if (!dryRun) {
          await writePlan(planned, ws);
        }
        return {
          status: "ok",
          output,
          durationMs: Date.now() - start,
          metadata: {
            evidenceKind: "patch",
            changedFiles: output.changedFiles,
            createdFiles: output.createdFiles,
            deletedFiles: output.deletedFiles,
            files: output.files,
            dryRun,
          },
        };
      } catch (err) {
        if (err instanceof PatchApplyError) {
          return {
            status: "error",
            errorCode: err.code,
            errorMessage: err.message,
            durationMs: Date.now() - start,
          };
        }
        if (isFsSafeEscape(err)) {
          return {
            status: "error",
            errorCode: "path_escape",
            errorMessage: `path escape detected: ${(err as Error).message}`,
            durationMs: Date.now() - start,
          };
        }
        return errorResult(err, start);
      }
    },
  };
}
