/**
 * FileWrite — overwrite a workspace file with given content.
 * Creates parent directories as needed.
 *
 * T1-03b: factory captures a default Workspace; execute() resolves the
 * effective workspace from ctx.spawnWorkspace (children) or that default.
 */

import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";
import { writeSafe, isFsSafeEscape } from "../util/fsSafe.js";
import {
  isLongTermMemoryWriteDisabled,
  isProtectedMemoryPath,
  protectedMemoryError,
} from "../util/memoryMode.js";
import { detectLazyComments } from "./fuzzyEdit.js";

export interface FileWriteInput {
  path: string;
  content: string;
}

export interface FileWriteOutput {
  path: string;
  bytesWritten: number;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    path: { type: "string", description: "Workspace-relative path to write." },
    content: { type: "string", description: "Full file content." },
  },
  required: ["path", "content"],
} as const;

export function makeFileWriteTool(workspaceRoot: string): Tool<FileWriteInput, FileWriteOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "FileWrite",
    description:
      "Write (overwrite) a workspace file. Parent directories are created if missing. Use FileEdit for surgical changes to existing files.",
    inputSchema: INPUT_SCHEMA,
    permission: "write",
    validate(input) {
      if (!input || typeof input.path !== "string" || input.path.length === 0) {
        return "`path` is required";
      }
      if (typeof input.content !== "string") return "`content` must be a string";
      return null;
    },
    async execute(
      input: FileWriteInput,
      ctx: ToolContext,
    ): Promise<ToolResult<FileWriteOutput>> {
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
        const lineCount = input.content.split("\n").length;
        if (lineCount < 500) {
          const lazyDetection = detectLazyComments(input.content);
          if (lazyDetection) {
            return {
              status: "error",
              errorCode: "lazy_output",
              errorMessage:
                `content contains a placeholder comment at line ${lazyDetection.line}: "${lazyDetection.matchedText}". ` +
                "Write the complete file content instead of using placeholder comments.",
              durationMs: Date.now() - start,
            };
          }
        }

        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        // Pre-create parent directory via Workspace.resolve — parent
        // creation is idempotent and benign even if the final write
        // path escapes (writeSafe catches the escape next).
        const resolved = ws.resolve(input.path);
        await fs.mkdir(path.dirname(resolved), { recursive: true });
        // Safe write — FD-based realpath re-validates post-open to
        // block symlink-swap TOCTOU (§15.2).
        await writeSafe(input.path, input.content, ws.root);
        return {
          status: "ok",
          output: {
            path: input.path,
            bytesWritten: Buffer.byteLength(input.content, "utf8"),
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
