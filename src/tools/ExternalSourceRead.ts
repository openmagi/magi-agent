import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type { SourceLedgerKind } from "../research/SourceLedger.js";
import { isFsSafeEscape, readSafe, statSafe } from "../util/fsSafe.js";
import { errorResult } from "../util/toolResult.js";

export interface ExternalSourceReadInput {
  source: string;
  path: string;
  offset?: number;
  limit?: number;
}

export interface ExternalSourceReadOutput {
  source: string;
  path: string;
  uri: string;
  content: string;
  sizeBytes: number;
  contentSha256: string;
  fileSha256: string;
  truncated: boolean;
  sourceId?: string;
}

interface ExternalSourceReadOptions {
  cacheRoot?: string;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    source: {
      type: "string",
      description:
        "External cache source directory, for example github.com/anomalyco/opencode.",
    },
    path: {
      type: "string",
      description: "Source-relative file path to read from the external cache.",
    },
    offset: { type: "integer", minimum: 1, description: "1-based line to start at." },
    limit: { type: "integer", minimum: 1, description: "Max lines to return." },
  },
  required: ["source", "path"],
  additionalProperties: false,
} as const;

const MAX_BYTES = 2 * 1024 * 1024;

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function defaultCacheRoot(): string {
  return process.env.MAGI_EXTERNAL_SOURCE_CACHE_ROOT
    ?? path.join(os.tmpdir(), "magi-external-sources");
}

function isUnderRoot(absPath: string, absRoot: string): boolean {
  return absPath === absRoot || absPath.startsWith(`${absRoot}${path.sep}`);
}

function normalizeRelative(value: string): string {
  return path.normalize(value).replace(/^[/\\]+/, "");
}

function resolveInside(root: string, relPath: string): string | null {
  const absRoot = path.resolve(root);
  const resolved = path.resolve(absRoot, normalizeRelative(relPath));
  return isUnderRoot(resolved, absRoot) ? resolved : null;
}

function externalUri(source: string, filePath: string): string {
  return `external:${source.replace(/\/+$/, "")}/${filePath.replace(/^\/+/, "")}`;
}

function contentHash(content: string): string {
  return `sha256:${crypto.createHash("sha256").update(content).digest("hex")}`;
}

function ledgerKindForSource(source: string): SourceLedgerKind {
  return source.startsWith("docs/") ? "external_doc" : "external_repo";
}

function applyLineWindow(raw: string, offset?: number, limit?: number): string {
  if (!offset && !limit) return raw;
  const lines = raw.split("\n");
  const off = Math.max(0, (offset ?? 1) - 1);
  const lim = limit ?? lines.length - off;
  return lines.slice(off, off + lim).join("\n");
}

export function makeExternalSourceReadTool(
  opts: ExternalSourceReadOptions = {},
): Tool<ExternalSourceReadInput, ExternalSourceReadOutput> {
  const cacheRoot = opts.cacheRoot ?? defaultCacheRoot();
  return {
    name: "ExternalSourceRead",
    description:
      "Read a file from the managed external repo/docs cache and register it as inspected source evidence. The cache must already be populated; this tool never clones, fetches, or mutates repositories.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    mutatesWorkspace: false,
    isConcurrencySafe: true,
    validate(input) {
      if (!input || !stringValue(input.source)) return "`source` is required";
      if (!stringValue(input.path)) return "`path` is required";
      return null;
    },
    async execute(
      input: ExternalSourceReadInput,
      ctx: ToolContext,
    ): Promise<ToolResult<ExternalSourceReadOutput>> {
      const start = Date.now();
      const source = stringValue(input.source) ?? "";
      const filePath = stringValue(input.path) ?? "";
      if (!source) {
        return {
          status: "error",
          errorCode: "invalid_input",
          errorMessage: "`source` is required",
          durationMs: Date.now() - start,
        };
      }
      if (!filePath) {
        return {
          status: "error",
          errorCode: "invalid_input",
          errorMessage: "`path` is required",
          durationMs: Date.now() - start,
        };
      }

      const sourceRoot = resolveInside(cacheRoot, source);
      if (!sourceRoot) {
        return {
          status: "error",
          errorCode: "path_escape",
          errorMessage: `source escapes external source cache: ${source}`,
          durationMs: Date.now() - start,
        };
      }
      const resolvedFile = resolveInside(sourceRoot, filePath);
      if (!resolvedFile) {
        return {
          status: "error",
          errorCode: "path_escape",
          errorMessage: `path escapes external source cache source: ${filePath}`,
          durationMs: Date.now() - start,
        };
      }

      try {
        await fs.mkdir(cacheRoot, { recursive: true });
        const relPath = path.relative(sourceRoot, resolvedFile);
        const stat = await statSafe(relPath, sourceRoot);
        if (!stat) {
          return {
            status: "error",
            errorCode: "not_found",
            errorMessage: `${source}/${filePath} not found in external source cache`,
            durationMs: Date.now() - start,
          };
        }
        if (!stat.isFile()) {
          return {
            status: "error",
            errorCode: "not_a_file",
            errorMessage: `${source}/${filePath} is not a regular file`,
            durationMs: Date.now() - start,
          };
        }
        const raw = await readSafe(relPath, sourceRoot);
        let content = applyLineWindow(raw, input.offset, input.limit);
        let truncated = false;
        if (Buffer.byteLength(content, "utf8") > MAX_BYTES) {
          content = content.slice(0, MAX_BYTES);
          truncated = true;
        }
        const uri = externalUri(source, filePath);
        const title = `${source.replace(/\/+$/, "")}/${filePath.replace(/^\/+/, "")}`;
        const ledgerSource = ctx.sourceLedger?.recordSource({
          turnId: ctx.turnId,
          toolName: "ExternalSourceRead",
          kind: ledgerKindForSource(source),
          uri,
          title,
          contentHash: contentHash(content),
          contentType: "text/plain",
          trustTier: "unknown",
          snippets: content ? [content.slice(0, 500)] : [],
          metadata: {
            source,
            path: filePath,
            truncated,
          },
        });
        if (ledgerSource) {
          ctx.emitAgentEvent?.({ type: "source_inspected", source: ledgerSource });
        }
        return {
          status: "ok",
          output: {
            source,
            path: filePath,
            uri,
            content,
            sizeBytes: stat.size,
            fileSha256: crypto.createHash("sha256").update(raw).digest("hex"),
            contentSha256: crypto.createHash("sha256").update(content).digest("hex"),
            truncated,
            ...(ledgerSource ? { sourceId: ledgerSource.sourceId } : {}),
          },
          durationMs: Date.now() - start,
          ...(ledgerSource ? { metadata: { sourceId: ledgerSource.sourceId } } : {}),
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
