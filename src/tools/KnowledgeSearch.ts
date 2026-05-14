import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { runLocalKnowledgeCommand } from "../knowledge/LocalKnowledgeBase.js";
import { Utf8StreamCapture } from "../util/Utf8StreamCapture.js";
import { withMagiBinPath } from "../util/shellPath.js";

type KnowledgeSearchMode =
  | "search"
  | "collections"
  | "documents"
  | "manifest"
  | "guide"
  | "get";

export type KnowledgeSearchScope = "all" | "personal" | "org";

export interface KnowledgeSearchInput {
  mode?: KnowledgeSearchMode;
  query?: string;
  collection?: string;
  scope?: KnowledgeSearchScope;
  limit?: number;
  objectKey?: string;
  maxBytes?: number;
  timeoutMs?: number;
}

export interface KnowledgeSearchRunResult {
  exitCode: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
  truncated: boolean;
}

export type KnowledgeSearchRunner = (
  args: string[],
  ctx: ToolContext,
  timeoutMs: number,
  extraEnv?: Record<string, string>,
) => Promise<KnowledgeSearchRunResult>;

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    mode: {
      type: "string",
      enum: ["search", "collections", "documents", "manifest", "guide", "get"],
      description: "KB operation. Defaults to search.",
    },
    query: {
      type: "string",
      description: "Search keywords. Required for mode=search.",
    },
    collection: {
      type: "string",
      description: "Optional collection name for search/manifest/documents; required for guide.",
    },
    scope: {
      type: "string",
      enum: ["all", "personal", "org"],
      description: "Filter by KB scope. 'personal' = only this bot's KB, 'org' = only shared org KB, 'all' = both (default).",
    },
    limit: {
      type: "integer",
      minimum: 1,
      maximum: 50,
      description: "Search result limit. Defaults to 10.",
    },
    objectKey: {
      type: "string",
      description: "Converted object key from manifest/search results. Required for mode=get.",
    },
    maxBytes: {
      type: "integer",
      minimum: 1,
      maximum: 524288,
      description: "Optional byte cap for mode=get output. Defaults to 128KB and is capped at 512KB.",
    },
    timeoutMs: {
      type: "integer",
      minimum: 100,
      maximum: 600000,
      description: "Timeout in ms. Defaults to 120000.",
    },
  },
  additionalProperties: false,
} as const;

const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_TIMEOUT_MS = 600_000;
const MAX_OUTPUT_BYTES = 128 * 1024;
const DEFAULT_GET_OUTPUT_BYTES = 128 * 1024;
const MAX_GET_OUTPUT_BYTES = 512 * 1024;

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function normalizeLimit(limit: unknown): string {
  if (typeof limit !== "number" || !Number.isFinite(limit)) return "10";
  return String(Math.max(1, Math.min(50, Math.trunc(limit))));
}

function modeOf(input: KnowledgeSearchInput): KnowledgeSearchMode {
  return input.mode ?? "search";
}

function outputLimitForArgs(args: string[]): number {
  return args[0] === "--get" ? MAX_GET_OUTPUT_BYTES : MAX_OUTPUT_BYTES;
}

function normalizeGetMaxBytes(input: KnowledgeSearchInput): number {
  if (typeof input.maxBytes !== "number" || !Number.isFinite(input.maxBytes)) {
    return DEFAULT_GET_OUTPUT_BYTES;
  }
  return Math.max(1, Math.min(MAX_GET_OUTPUT_BYTES, Math.trunc(input.maxBytes)));
}

function contentHash(content: string): string {
  return `sha256:${createHash("sha256").update(content).digest("hex")}`;
}

function truncateOutput(output: string, maxBytes: number): {
  output: string;
  truncated: boolean;
} {
  if (Buffer.byteLength(output, "utf8") <= maxBytes) {
    return { output, truncated: false };
  }
  const head = Buffer.from(output, "utf8").subarray(0, maxBytes).toString("utf8");
  return {
    output: `${head}\n\n[KB content truncated to ${maxBytes} bytes. Narrow the query or fetch a smaller section before synthesizing.]`,
    truncated: true,
  };
}

function normalizeComparable(value: unknown): string {
  return typeof value === "string" ? value.trim().normalize("NFC") : "";
}

function keyBasename(value: string): string {
  return value.split(/[\\/]/).filter(Boolean).pop() ?? value;
}

function candidateDocumentKeys(document: unknown): string[] {
  if (!document || typeof document !== "object") return [];
  const record = document as Record<string, unknown>;
  return [
    record.object_key_converted,
    record.object_key_original,
    record.objectKey,
    record.converted_object_key,
  ].flatMap((value) => (typeof value === "string" && value.trim() ? [value.trim()] : []));
}

function parseDocuments(stdout: string): unknown[] {
  try {
    const parsed = JSON.parse(stdout);
    return Array.isArray(parsed?.documents) ? parsed.documents : [];
  } catch {
    return [];
  }
}

function parseJsonObject(stdout: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(stdout);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function documentText(document: unknown): string {
  if (!document || typeof document !== "object") return "";
  const record = document as Record<string, unknown>;
  const values = [
    record.filename,
    record.canonical_filename,
    record.canonical_title,
    record.object_key_converted,
    ...(Array.isArray(record.aliases) ? record.aliases : []),
    ...(Array.isArray(record.search_hints) ? record.search_hints : []),
  ];
  return values
    .filter((value): value is string => typeof value === "string")
    .join(" ")
    .normalize("NFC")
    .toLocaleLowerCase();
}

function queryTerms(query: string): string[] {
  return query
    .normalize("NFC")
    .toLocaleLowerCase()
    .split(/[^\p{L}\p{N}_-]+/u)
    .map((term) => term.trim())
    .filter((term) => term.length >= 2);
}

function summarizeDocumentMatch(document: unknown): Record<string, unknown> {
  const record = document && typeof document === "object"
    ? document as Record<string, unknown>
    : {};
  return {
    id: record.id ?? null,
    filename: record.filename ?? record.canonical_filename ?? record.canonical_title ?? null,
    canonical_title: record.canonical_title ?? null,
    status: record.status ?? null,
    converted_size: record.converted_size ?? null,
    chunk_count: record.chunk_count ?? null,
    object_key_converted: record.object_key_converted ?? null,
  };
}

function findDocumentMatches(query: string, documents: unknown[], limit: number): Record<string, unknown>[] {
  const terms = queryTerms(query);
  if (terms.length === 0) return [];
  const matches: Record<string, unknown>[] = [];
  const seen = new Set<string>();
  for (const document of documents) {
    const text = documentText(document);
    if (!terms.some((term) => text.includes(term))) continue;
    const summary = summarizeDocumentMatch(document);
    const key = String(summary.object_key_converted ?? summary.filename ?? JSON.stringify(summary));
    if (seen.has(key)) continue;
    seen.add(key);
    matches.push(summary);
    if (matches.length >= limit) break;
  }
  return matches;
}

function hasEmptySearchResults(output: Record<string, unknown> | null): boolean {
  return Boolean(output && Array.isArray(output.results) && output.results.length === 0);
}

function findNormalizedObjectKey(requestedObjectKey: string, documents: unknown[]): string | null {
  const requested = normalizeComparable(requestedObjectKey);
  const requestedBase = normalizeComparable(keyBasename(requestedObjectKey));
  for (const document of documents) {
    for (const key of candidateDocumentKeys(document)) {
      const comparableKey = normalizeComparable(key);
      if (comparableKey === requested) return key;
      if (requestedBase && normalizeComparable(keyBasename(key)) === requestedBase) return key;
    }
  }
  return null;
}

export function validateKnowledgeSearchInput(input: KnowledgeSearchInput): string | null {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return "`input` must be an object";
  }
  const mode = modeOf(input);
  if (!["search", "collections", "documents", "manifest", "guide", "get"].includes(mode)) {
    return "`mode` must be search, collections, documents, manifest, guide, or get";
  }
  if (mode === "search" && !stringValue(input.query)) {
    return "`query` is required for knowledge search";
  }
  if (mode === "guide" && !stringValue(input.collection)) {
    return "`collection` is required for knowledge guide";
  }
  if (mode === "get" && !stringValue(input.objectKey)) {
    return "`objectKey` is required for knowledge get";
  }
  return null;
}

export function buildKnowledgeSearchArgs(input: KnowledgeSearchInput): string[] {
  const mode = modeOf(input);
  const collection = stringValue(input.collection);
  switch (mode) {
    case "collections":
      return ["--collections"];
    case "documents":
      return collection ? ["--documents", collection] : ["--documents"];
    case "manifest":
      return collection ? ["--manifest", collection] : ["--manifest"];
    case "guide":
      return ["--guide", stringValue(input.collection) ?? ""];
    case "get":
      return ["--get", stringValue(input.objectKey) ?? ""];
    case "search": {
      const query = stringValue(input.query) ?? "";
      const limit = normalizeLimit(input.limit);
      return collection ? [collection, query, limit] : ["", query, limit];
    }
  }
}

async function externalRunner(
  command: string,
  args: string[],
  ctx: ToolContext,
  timeoutMs: number,
  extraEnv?: Record<string, string>,
): Promise<KnowledgeSearchRunResult> {
  const cwd = ctx.spawnWorkspace?.root ?? ctx.workspaceRoot;
  const maxOutputBytes = outputLimitForArgs(args);
  return new Promise<KnowledgeSearchRunResult>((resolve) => {
    const child = spawn(command, args, {
      cwd,
      env: {
        ...withMagiBinPath(process.env),
        PWD: cwd,
        BOT_ID: process.env.BOT_ID ?? ctx.botId,
        MAGI_WORKSPACE_ROOT: cwd,
        MAGI_BOT_ID: ctx.botId,
        ...extraEnv,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });

    const stdout = new Utf8StreamCapture(maxOutputBytes);
    const stderr = new Utf8StreamCapture(maxOutputBytes);

    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 3_000).unref();
    }, timeoutMs);

    let settled = false;
    const abort = (): void => {
      child.kill("SIGTERM");
    };
    ctx.abortSignal.addEventListener("abort", abort, { once: true });

    child.stdout.on("data", (chunk: Buffer) => stdout.write(chunk));
    child.stderr.on("data", (chunk: Buffer) => stderr.write(chunk));
    child.on("close", (exitCode, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      ctx.abortSignal.removeEventListener("abort", abort);
      resolve({
        exitCode,
        signal,
        stdout: stdout.end(),
        stderr: stderr.end(),
        truncated: stdout.truncated || stderr.truncated,
      });
    });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      ctx.abortSignal.removeEventListener("abort", abort);
      resolve({
        exitCode: null,
        signal: null,
        stdout: stdout.end(),
        stderr: error instanceof Error ? error.message : String(error),
        truncated: stdout.truncated || stderr.truncated,
      });
    });
  });
}

async function defaultRunner(
  args: string[],
  ctx: ToolContext,
  timeoutMs: number,
  extraEnv?: Record<string, string>,
): Promise<KnowledgeSearchRunResult> {
  const externalCommand =
    process.env.MAGI_KB_SEARCH_COMMAND?.trim() ||
    process.env.CORE_AGENT_KB_SEARCH_COMMAND?.trim();
  if (externalCommand) {
    return externalRunner(externalCommand, args, ctx, timeoutMs, extraEnv);
  }
  return runLocalKnowledgeCommand(args, ctx);
}

export function makeKnowledgeSearchTool(opts: {
  name?: "knowledge-search" | "KnowledgeSearch";
  runner?: KnowledgeSearchRunner;
  command?: string;
} = {}): Tool<KnowledgeSearchInput, string> {
  const runner = opts.runner ??
    ((args: string[], ctx: ToolContext, timeoutMs: number, extraEnv?: Record<string, string>) =>
      defaultRunner(args, ctx, timeoutMs, extraEnv));
  return {
    name: opts.name ?? "knowledge-search",
    description:
      "Search and inspect the user's local workspace Knowledge Base under workspace/knowledge. Use this for /kb, uploaded documents, collection manifests, and document-backed answers.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    dangerous: false,
    tags: ["knowledge", "kb", "documents"],
    validate(input) {
      return validateKnowledgeSearchInput(input as KnowledgeSearchInput);
    },
    async execute(input: KnowledgeSearchInput, ctx: ToolContext): Promise<ToolResult<string>> {
      const start = Date.now();
      const validation = validateKnowledgeSearchInput(input);
      if (validation) {
        return {
          status: "error",
          errorCode: "invalid_input",
          errorMessage: validation,
          durationMs: Date.now() - start,
        };
      }
      const timeoutMs = Math.min(MAX_TIMEOUT_MS, input.timeoutMs ?? DEFAULT_TIMEOUT_MS);
      const mode = modeOf(input);
      let args = buildKnowledgeSearchArgs(input);
      const scopeEnv = input.scope && input.scope !== "all"
        ? { KB_SCOPE: input.scope }
        : undefined;
      let result = await runner(args, ctx, timeoutMs, scopeEnv);
      let augmentedOutput: string | null = null;
      let usedDocumentFallback = false;
      if (mode === "search" && result.exitCode === 0) {
        const parsedSearch = parseJsonObject(result.stdout);
        if (hasEmptySearchResults(parsedSearch)) {
          const documentsArgs = stringValue(input.collection)
            ? ["--documents", stringValue(input.collection) ?? ""]
            : ["--documents"];
          const documentsResult = await runner(documentsArgs, ctx, timeoutMs, scopeEnv);
          if (documentsResult.exitCode === 0) {
            const matches = findDocumentMatches(
              stringValue(input.query) ?? "",
              parseDocuments(documentsResult.stdout),
              Number.parseInt(normalizeLimit(input.limit), 10),
            );
            if (matches.length > 0) {
              augmentedOutput = JSON.stringify({
                ...parsedSearch,
                document_matches: matches,
                document_match_note:
                  "Full-text KB search returned no chunks, but matching document names/aliases were found. Use mode=get with object_key_converted to inspect them.",
              });
              usedDocumentFallback = true;
            }
          }
        }
      }
      if (mode === "get" && result.exitCode !== 0 && stringValue(input.objectKey)) {
        const documentsArgs = stringValue(input.collection)
          ? ["--documents", stringValue(input.collection) ?? ""]
          : ["--documents"];
        const documentsResult = await runner(documentsArgs, ctx, timeoutMs, scopeEnv);
        if (documentsResult.exitCode === 0) {
          const repairedObjectKey = findNormalizedObjectKey(
            stringValue(input.objectKey) ?? "",
            parseDocuments(documentsResult.stdout),
          );
          if (repairedObjectKey && repairedObjectKey !== stringValue(input.objectKey)) {
            args = ["--get", repairedObjectKey];
            result = await runner(args, ctx, timeoutMs, scopeEnv);
          }
        }
      }
      const rawOutput = augmentedOutput ?? result.stdout.trim();
      const capped = mode === "get"
        ? truncateOutput(rawOutput, normalizeGetMaxBytes(input))
        : { output: rawOutput, truncated: false };
      const output = capped.output;
      const error = result.stderr.trim();
      const source = result.exitCode === 0 && mode === "get"
        ? ctx.sourceLedger?.recordSource({
            turnId: ctx.turnId,
            toolName: opts.name ?? "knowledge-search",
            kind: "kb",
            uri: `kb:${args[1] ?? stringValue(input.objectKey) ?? ""}`,
            title: keyBasename(args[1] ?? stringValue(input.objectKey) ?? ""),
            contentHash: contentHash(output),
            contentType: "text/markdown",
            trustTier: "unknown",
            snippets: output ? [output.slice(0, 500)] : [],
            metadata: {
              args,
              truncated: result.truncated || capped.truncated,
            },
          })
        : undefined;
      if (source) {
        ctx.emitAgentEvent?.({ type: "source_inspected", source });
      }
      return {
        status: result.exitCode === 0 ? "ok" : "error",
        output: result.exitCode === 0 ? output : undefined,
        errorCode: result.exitCode === 0
          ? undefined
          : result.exitCode === null
            ? "spawn_error"
            : `exit_${result.exitCode}`,
        errorMessage: result.exitCode === 0
          ? undefined
          : error || output || `kb-search.sh exited ${result.exitCode}`,
        durationMs: Date.now() - start,
        metadata: {
          args,
          ...(source ? { sourceId: source.sourceId } : {}),
          signal: result.signal,
          truncated: result.truncated || capped.truncated,
          documentFallback: usedDocumentFallback,
        },
      };
    },
  };
}
