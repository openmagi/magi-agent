/**
 * FileEdit — find-and-replace within a workspace file.
 * Fails if `old_string` is not unique (unless `replace_all`).
 *
 * T1-03b: resolves ctx.spawnWorkspace ?? defaultWorkspace per call.
 */

import fs from "node:fs/promises";
import crypto from "node:crypto";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";
import { readSafe, writeSafe, isFsSafeEscape } from "../util/fsSafe.js";
import {
  isLongTermMemoryWriteDisabled,
  isProtectedMemoryPath,
  protectedMemoryError,
} from "../util/memoryMode.js";

export interface FileEditInput {
  path: string;
  old_string: string;
  new_string: string;
  replace_all?: boolean;
  expected_file_sha256?: string;
}

export interface FileEditOutput {
  path: string;
  replaced: number;
  patch: FileEditPatchEvidence;
}

export interface FileEditPatchHunk {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  oldText: string;
  newText: string;
}

export interface FileEditPatchEvidence {
  path: string;
  oldSha256: string;
  newSha256: string;
  replaced: number;
  hunks: FileEditPatchHunk[];
  changedSymbols: string[];
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    path: { type: "string", description: "Workspace-relative path." },
    old_string: { type: "string", description: "Exact text to find (including whitespace)." },
    new_string: { type: "string", description: "Replacement text." },
    replace_all: {
      type: "boolean",
      default: false,
      description: "Replace every occurrence. Default false — fails if old_string is not unique.",
    },
    expected_file_sha256: {
      type: "string",
      description:
        "Optional sha256 of the full file content from FileRead.fileSha256. When provided, FileEdit refuses to write if the file changed since that read.",
    },
  },
  required: ["path", "old_string", "new_string"],
} as const;

export function makeFileEditTool(workspaceRoot: string): Tool<FileEditInput, FileEditOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "FileEdit",
    description:
      "Find-and-replace within a workspace file. old_string must match exactly once unless replace_all=true. Use expected_file_sha256 from FileRead.fileSha256 to prevent stale edits. Use this instead of FileWrite when only a small change is needed.",
    inputSchema: INPUT_SCHEMA,
    permission: "write",
    validate(input) {
      if (!input || typeof input.path !== "string") return "`path` is required";
      if (typeof input.old_string !== "string" || input.old_string.length === 0) {
        return "`old_string` is required (non-empty)";
      }
      if (typeof input.new_string !== "string") return "`new_string` must be a string";
      if (input.old_string === input.new_string) {
        return "`old_string` and `new_string` must differ";
      }
      if (
        input.expected_file_sha256 !== undefined &&
        !/^[a-f0-9]{64}$/i.test(input.expected_file_sha256)
      ) {
        return "`expected_file_sha256` must be a 64-character hex sha256";
      }
      return null;
    },
    async execute(
      input: FileEditInput,
      ctx: ToolContext,
    ): Promise<ToolResult<FileEditOutput>> {
      const start = Date.now();
      if (isLongTermMemoryWriteDisabled(ctx.memoryMode) && isProtectedMemoryPath(input.path)) {
        return {
          status: "permission_denied",
          errorCode: "memory_write_blocked",
          errorMessage: protectedMemoryError(input.path),
          durationMs: Date.now() - start,
        };
      }
      try {
        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        // Safe read — FD-based realpath blocks symlink-swap TOCTOU.
        const content = await readSafe(input.path, ws.root);
        if (
          input.expected_file_sha256 !== undefined &&
          sha256(content) !== input.expected_file_sha256.toLowerCase()
        ) {
          return {
            status: "error",
            errorCode: "stale_file",
            errorMessage: `${input.path} changed since the expected FileRead snapshot; read the file again before editing`,
            durationMs: Date.now() - start,
          };
        }
        const fileLineEnding = dominantLineEnding(content);
        const oldString = normalizeLineEndingsForFile(input.old_string, fileLineEnding);
        const newString = normalizeLineEndingsForFile(input.new_string, fileLineEnding);
        let next = content;
        let replaced = 0;
        let replacementOffsets: number[] = [];
        if (input.replace_all) {
          replacementOffsets = findAllOccurrences(content, oldString);
          const parts = content.split(oldString);
          replaced = parts.length - 1;
          if (replaced === 0) {
            return {
              status: "error",
              errorCode: "not_found",
              errorMessage: `old_string not found in ${input.path}`,
              durationMs: Date.now() - start,
            };
          }
          next = parts.join(newString);
        } else {
          const first = content.indexOf(oldString);
          if (first < 0) {
            return {
              status: "error",
              errorCode: "not_found",
              errorMessage: `old_string not found in ${input.path}`,
              durationMs: Date.now() - start,
            };
          }
          const second = content.indexOf(oldString, first + oldString.length);
          if (second >= 0) {
            return {
              status: "error",
              errorCode: "not_unique",
              errorMessage: `old_string appears more than once in ${input.path}; use replace_all or extend with surrounding context`,
              durationMs: Date.now() - start,
            };
          }
          next =
            content.slice(0, first) + newString + content.slice(first + oldString.length);
          replaced = 1;
          replacementOffsets = [first];
        }
        // Safe write — re-validate via FD realpath before committing.
        await writeSafe(input.path, next, ws.root);
        const patch = buildPatchEvidence({
          path: input.path,
          before: content,
          after: next,
          oldString,
          newString,
          replacementOffsets,
          replaced,
        });
        return {
          status: "ok",
          output: { path: input.path, replaced, patch },
          metadata: {
            evidenceKind: "patch",
            changedFiles: [input.path],
            patch,
          },
          durationMs: Date.now() - start,
        };
      } catch (err) {
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

function sha256(value: string): string {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function findAllOccurrences(content: string, needle: string): number[] {
  const offsets: number[] = [];
  let pos = 0;
  for (;;) {
    const found = content.indexOf(needle, pos);
    if (found < 0) return offsets;
    offsets.push(found);
    pos = found + Math.max(1, needle.length);
  }
}

function dominantLineEnding(content: string): "\r\n" | "\n" | null {
  const crlf = content.match(/\r\n/g)?.length ?? 0;
  const lf = (content.match(/\n/g)?.length ?? 0) - crlf;
  if (crlf === 0 && lf === 0) return null;
  return crlf > lf ? "\r\n" : "\n";
}

function normalizeLineEndingsForFile(value: string, fileLineEnding: "\r\n" | "\n" | null): string {
  if (fileLineEnding === null) return value;
  const lf = value.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  return fileLineEnding === "\r\n" ? lf.replace(/\n/g, "\r\n") : lf;
}

function oneBasedLineAt(content: string, offset: number): number {
  if (offset <= 0) return 1;
  let line = 1;
  for (let i = 0; i < offset && i < content.length; i++) {
    if (content.charCodeAt(i) === 10) line++;
  }
  return line;
}

function lineCount(text: string): number {
  if (text.length === 0) return 0;
  return text.split("\n").length;
}

function buildPatchEvidence(input: {
  path: string;
  before: string;
  after: string;
  oldString: string;
  newString: string;
  replacementOffsets: number[];
  replaced: number;
}): FileEditPatchEvidence {
  const oldLines = lineCount(input.oldString);
  const newLines = lineCount(input.newString);
  const lineDelta = newLines - oldLines;
  let cumulativeDelta = 0;
  const hunks = input.replacementOffsets.map((offset) => {
    const oldStart = oneBasedLineAt(input.before, offset);
    const hunk: FileEditPatchHunk = {
      oldStart,
      oldLines,
      newStart: oldStart + cumulativeDelta,
      newLines,
      oldText: input.oldString,
      newText: input.newString,
    };
    cumulativeDelta += lineDelta;
    return hunk;
  });
  return {
    path: input.path,
    oldSha256: sha256(input.before),
    newSha256: sha256(input.after),
    replaced: input.replaced,
    hunks,
    changedSymbols: changedSymbolsForHunks(input.before, hunks),
  };
}

function changedSymbolsForHunks(
  before: string,
  hunks: readonly FileEditPatchHunk[],
): string[] {
  const lines = before.split("\n");
  const symbols = new Set<string>();
  for (const hunk of hunks) {
    const name = nearestSymbolBeforeLine(lines, hunk.oldStart);
    if (name) symbols.add(name);
  }
  return [...symbols].sort();
}

function nearestSymbolBeforeLine(lines: readonly string[], oneBasedLine: number): string | null {
  const patterns = [
    /\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\b/,
    /\b(?:export\s+)?class\s+([A-Za-z_$][\w$]*)\b/,
    /\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=/,
  ];
  for (let i = Math.min(lines.length, oneBasedLine) - 1; i >= 0; i--) {
    const line = lines[i] ?? "";
    for (const pattern of patterns) {
      const match = pattern.exec(line);
      if (match?.[1]) return match[1];
    }
  }
  return null;
}
