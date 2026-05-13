/**
 * FileRead — read a workspace file as text.
 *
 * T1-03b: factory captures a default Workspace over `workspaceRoot`; at
 * runtime every execute() consults `ctx.spawnWorkspace ?? defaultWorkspace`
 * so spawned children are scoped to their ephemeral subdirectory rather
 * than the parent's full PVC root (PRE-01 completion).
 */

import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";
import { readSafe, statSafe, isFsSafeEscape } from "../util/fsSafe.js";
import {
  isIncognitoMemoryMode,
  isProtectedMemoryPath,
  protectedMemoryError,
} from "../util/memoryMode.js";

export interface FileReadInput {
  path: string;
  /** Optional 1-based line offset. */
  offset?: number;
  /** Optional line count. */
  limit?: number;
}

export interface FileReadOutput {
  path: string;
  content: string;
  sizeBytes: number;
  contentSha256: string;
  fileSha256: string;
  truncated: boolean;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    path: { type: "string", description: "Workspace-relative path to read." },
    offset: { type: "integer", minimum: 1, description: "1-based line to start at." },
    limit: { type: "integer", minimum: 1, description: "Max lines to return." },
  },
  required: ["path"],
} as const;

const MAX_BYTES = 2 * 1024 * 1024; // 2 MB hard cap

export function makeFileReadTool(workspaceRoot: string): Tool<FileReadInput, FileReadOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "FileRead",
    description:
      "Read a workspace file. Returns the full text unless offset/limit are given, in which case only those lines are returned.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    validate(input) {
      if (!input || typeof input.path !== "string" || input.path.length === 0) {
        return "`path` is required";
      }
      return null;
    },
    async execute(input: FileReadInput, ctx: ToolContext): Promise<ToolResult<FileReadOutput>> {
      const start = Date.now();
      if (isIncognitoMemoryMode(ctx.memoryMode) && isProtectedMemoryPath(input.path)) {
        return {
          status: "permission_denied",
          errorCode: "incognito_memory_blocked",
          errorMessage: protectedMemoryError(input.path),
          durationMs: Date.now() - start,
        };
      }
      try {
        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        // Safe stat + read — opens once, re-validates via FD realpath,
        // closes. Blocks symlink-swap TOCTOU (§15.2).
        const stat = await statSafe(input.path, ws.root);
        if (!stat) {
          return {
            status: "error",
            errorCode: "not_found",
            errorMessage: `${input.path} not found`,
            durationMs: Date.now() - start,
          };
        }
        if (!stat.isFile()) {
          return {
            status: "error",
            errorCode: "not_a_file",
            errorMessage: `${input.path} is not a regular file`,
            durationMs: Date.now() - start,
          };
        }
        const raw = await readSafe(input.path, ws.root);
        let content = raw;
        let truncated = false;
        if (input.offset || input.limit) {
          const lines = raw.split("\n");
          const off = Math.max(0, (input.offset ?? 1) - 1);
          const lim = input.limit ?? lines.length - off;
          content = lines.slice(off, off + lim).join("\n");
        }
        if (Buffer.byteLength(content, "utf8") > MAX_BYTES) {
          content = content.slice(0, MAX_BYTES);
          truncated = true;
        }
        const source = ctx.sourceLedger?.recordSource({
          turnId: ctx.turnId,
          toolName: "FileRead",
          kind: "file",
          uri: `file:${input.path}`,
          title: input.path,
          contentHash: `sha256:${crypto.createHash("sha256").update(content).digest("hex")}`,
          contentType: "text/plain",
          trustTier: "unknown",
          snippets: content ? [content.slice(0, 500)] : [],
          metadata: { truncated },
        });
        if (source) {
          ctx.emitAgentEvent?.({ type: "source_inspected", source });
        }
        return {
          status: "ok",
          output: {
            path: input.path,
            fileSha256: crypto.createHash("sha256").update(raw).digest("hex"),
            contentSha256: crypto.createHash("sha256").update(content).digest("hex"),
            content,
            sizeBytes: stat.size,
            truncated,
          },
          durationMs: Date.now() - start,
          ...(source ? { metadata: { sourceId: source.sourceId } } : {}),
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

/**
 * Legacy helper — retained because a few callers outside the 6 core
 * tools import it. New code should prefer `Workspace#resolve` directly.
 */
export function resolveInsideWorkspace(root: string, rel: string): string {
  const normalised = path.normalize(rel).replace(/^\/+/, "");
  const full = path.join(root, normalised);
  const absRoot = path.resolve(root);
  const absFull = path.resolve(full);
  if (absFull !== absRoot && !absFull.startsWith(absRoot + path.sep)) {
    throw new Error(`path escapes workspace: ${rel}`);
  }
  return absFull;
}
