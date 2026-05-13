/**
 * BatchRead — read multiple workspace files in a single tool call.
 *
 * Eliminates the N-iteration penalty when a model needs to inspect many
 * files. Each path is resolved independently; individual failures don't
 * block the rest of the batch.
 */

import crypto from "node:crypto";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { readSafe, statSafe, isFsSafeEscape } from "../util/fsSafe.js";
import {
  isIncognitoMemoryMode,
  isProtectedMemoryPath,
  protectedMemoryError,
} from "../util/memoryMode.js";

export interface BatchReadInput {
  paths: string[];
  offset?: number;
  limit?: number;
}

export interface BatchReadFileResult {
  path: string;
  status: "ok" | "error";
  content?: string;
  sizeBytes?: number;
  contentSha256?: string;
  truncated?: boolean;
  errorCode?: string;
  errorMessage?: string;
}

export interface BatchReadOutput {
  results: BatchReadFileResult[];
  truncatedBatch: boolean;
}

const MAX_PATHS = 20;
const MAX_BYTES_PER_FILE = 2 * 1024 * 1024;
const MAX_TOTAL_BYTES = 4 * 1024 * 1024;

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    paths: {
      type: "array",
      items: { type: "string" },
      minItems: 1,
      maxItems: MAX_PATHS,
      description: "Workspace-relative paths to read (max 20).",
    },
    offset: { type: "integer", minimum: 1, description: "1-based line to start at (applied to all files)." },
    limit: { type: "integer", minimum: 1, description: "Max lines to return per file." },
  },
  required: ["paths"],
} as const;

export function makeBatchReadTool(workspaceRoot: string): Tool<BatchReadInput, BatchReadOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "BatchRead",
    description:
      "Read multiple workspace files in one call (max 20). Returns content for each file independently — individual failures don't block others. Use when you need to inspect several files at once.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    isConcurrencySafe: true,
    validate(input) {
      if (!input || !Array.isArray(input.paths) || input.paths.length === 0) {
        return "`paths` must be a non-empty array of file paths";
      }
      if (input.paths.length > MAX_PATHS) {
        return `too many paths: ${input.paths.length} exceeds max ${MAX_PATHS}`;
      }
      return null;
    },
    async execute(input: BatchReadInput, ctx: ToolContext): Promise<ToolResult<BatchReadOutput>> {
      const start = Date.now();

      if (input.paths.length > MAX_PATHS) {
        return {
          status: "error",
          errorCode: "too_many_paths",
          errorMessage: `${input.paths.length} paths exceeds max ${MAX_PATHS}`,
          durationMs: Date.now() - start,
        };
      }

      const incognito = isIncognitoMemoryMode(ctx.memoryMode);
      if (incognito && input.paths.some((p) => isProtectedMemoryPath(p))) {
        return {
          status: "permission_denied",
          errorCode: "incognito_memory_blocked",
          errorMessage: protectedMemoryError(input.paths.find((p) => isProtectedMemoryPath(p))!),
          durationMs: Date.now() - start,
        };
      }

      const ws = ctx.spawnWorkspace ?? defaultWorkspace;
      const results: BatchReadFileResult[] = [];
      let totalBytes = 0;
      let truncatedBatch = false;

      for (const filePath of input.paths) {
        if (totalBytes >= MAX_TOTAL_BYTES) {
          truncatedBatch = true;
          results.push({
            path: filePath,
            status: "error",
            errorCode: "batch_size_exceeded",
            errorMessage: "batch total output cap reached",
          });
          continue;
        }

        try {
          const stat = await statSafe(filePath, ws.root);
          if (!stat) {
            results.push({ path: filePath, status: "error", errorCode: "not_found", errorMessage: `${filePath} not found` });
            continue;
          }
          if (!stat.isFile()) {
            results.push({ path: filePath, status: "error", errorCode: "not_a_file", errorMessage: `${filePath} is not a regular file` });
            continue;
          }

          const raw = await readSafe(filePath, ws.root);
          let content = raw;
          let truncated = false;

          if (input.offset || input.limit) {
            const lines = raw.split("\n");
            const off = Math.max(0, (input.offset ?? 1) - 1);
            const lim = input.limit ?? lines.length - off;
            content = lines.slice(off, off + lim).join("\n");
          }

          if (Buffer.byteLength(content, "utf8") > MAX_BYTES_PER_FILE) {
            content = content.slice(0, MAX_BYTES_PER_FILE);
            truncated = true;
          }

          const contentBytes = Buffer.byteLength(content, "utf8");
          totalBytes += contentBytes;

          results.push({
            path: filePath,
            status: "ok",
            content,
            sizeBytes: stat.size,
            contentSha256: crypto.createHash("sha256").update(content).digest("hex"),
            truncated,
          });
        } catch (err) {
          if (isFsSafeEscape(err)) {
            results.push({
              path: filePath,
              status: "error",
              errorCode: "path_escape",
              errorMessage: `path escape detected: ${(err as Error).message}`,
            });
          } else {
            results.push({
              path: filePath,
              status: "error",
              errorCode: "read_error",
              errorMessage: (err as Error).message,
            });
          }
        }
      }

      return {
        status: "ok",
        output: { results, truncatedBatch },
        durationMs: Date.now() - start,
      };
    },
  };
}
