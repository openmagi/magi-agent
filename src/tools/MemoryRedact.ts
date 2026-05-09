import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { readSafe, writeSafe, isFsSafeEscape } from "../util/fsSafe.js";
import { errorResult } from "../util/toolResult.js";

export interface MemoryRedactInput {
  mode: "redact";
  target_text: string;
  paths?: string[];
  replacement?: string;
  confirm_raw_redaction?: boolean;
  reason?: string;
}

export interface MemoryRedactFileResult {
  path: string;
  replacements: number;
  beforeSha256: string;
  afterSha256: string;
}

export interface MemoryRedactOutput {
  mode: "redact";
  targetSha256: string;
  matchedCount: number;
  changedFiles: string[];
  files: MemoryRedactFileResult[];
  auditPath: string;
  verification: {
    targetStillPresent: boolean;
  };
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    mode: { type: "string", enum: ["redact"], description: "Raw redact exact text from memory files." },
    target_text: { type: "string", description: "Exact text to remove from memory files." },
    paths: {
      type: "array",
      items: { type: "string" },
      description: "Optional workspace-relative memory paths to inspect. Defaults to memory files.",
    },
    replacement: {
      type: "string",
      description: "Replacement marker. Defaults to [redacted by user request].",
    },
    confirm_raw_redaction: {
      type: "boolean",
      description: "Must be true because raw memory redaction is destructive.",
    },
    reason: { type: "string", description: "User-facing reason for the audit record." },
  },
  required: ["mode", "target_text", "confirm_raw_redaction"],
} as const;

export function makeMemoryRedactTool(
  workspaceRoot: string,
  opts: { now?: () => Date } = {},
): Tool<MemoryRedactInput, MemoryRedactOutput> {
  const now = opts.now ?? (() => new Date());
  return {
    name: "MemoryRedact",
    description:
      "Delete or redact exact user-specified text from Hipocampus memory files. Use this for user requests to remove content from memory; do not edit memory files with FileEdit/Bash for this workflow.",
    inputSchema: INPUT_SCHEMA,
    permission: "write",
    dangerous: true,
    mutatesWorkspace: true,
    validate(input) {
      if (!input || input.mode !== "redact") return "`mode` must be `redact`";
      if (typeof input.target_text !== "string" || input.target_text.trim().length === 0) {
        return "`target_text` is required";
      }
      if (input.replacement !== undefined && typeof input.replacement !== "string") {
        return "`replacement` must be a string";
      }
      if (input.paths !== undefined) {
        if (!Array.isArray(input.paths)) return "`paths` must be an array";
        if (!input.paths.every((item) => typeof item === "string" && item.length > 0)) {
          return "`paths` must contain non-empty strings";
        }
      }
      return null;
    },
    async execute(
      input: MemoryRedactInput,
      ctx: ToolContext,
    ): Promise<ToolResult<MemoryRedactOutput>> {
      const started = Date.now();
      if (input.confirm_raw_redaction !== true) {
        return {
          status: "error",
          errorCode: "confirmation_required",
          errorMessage:
            "Raw memory redaction requires confirm_raw_redaction=true.",
          durationMs: Date.now() - started,
        };
      }

      const target = input.target_text;
      const replacement = input.replacement ?? "[redacted by user request]";
      if (replacement.includes(target)) {
        return {
          status: "error",
          errorCode: "invalid_replacement",
          errorMessage: "replacement must not contain target_text",
          durationMs: Date.now() - started,
        };
      }

      try {
        const paths = input.paths?.length
          ? normalizeExplicitPaths(input.paths)
          : await discoverMemoryFiles(workspaceRoot);
        const targetSha256 = sha256(target);
        const files: MemoryRedactFileResult[] = [];
        let matchedCount = 0;
        let targetStillPresent = false;

        for (const relPath of paths) {
          const before = await readSafe(relPath, workspaceRoot);
          const occurrences = countOccurrences(before, target);
          if (occurrences === 0) {
            if (before.includes(target)) targetStillPresent = true;
            continue;
          }
          const after = before.split(target).join(replacement);
          await writeSafe(relPath, after, workspaceRoot);
          matchedCount += occurrences;
          targetStillPresent = targetStillPresent || after.includes(target);
          files.push({
            path: relPath,
            replacements: occurrences,
            beforeSha256: sha256(before),
            afterSha256: sha256(after),
          });
        }

        const auditNow = now();
        const auditPath = await appendAuditRecord({
          workspaceRoot,
          now: auditNow,
          record: {
            ts: auditNow.toISOString(),
            sessionKey: ctx.sessionKey,
            turnId: ctx.turnId,
            mode: "redact",
            reason: input.reason ?? "",
            targetSha256,
            targetByteLength: Buffer.byteLength(target, "utf8"),
            replacementSha256: sha256(replacement),
            matchedCount,
            files,
            verification: { targetStillPresent },
          },
        });

        const output: MemoryRedactOutput = {
          mode: "redact",
          targetSha256,
          matchedCount,
          changedFiles: files.map((file) => file.path),
          files,
          auditPath,
          verification: { targetStillPresent },
        };
        ctx.staging.stageAuditEvent("memory_redact", {
          auditPath,
          matchedCount,
          changedFiles: output.changedFiles,
          targetSha256,
        });
        return {
          status: "ok",
          output,
          metadata: {
            evidenceKind: "memory_redaction",
            changedFiles: output.changedFiles,
            auditPath,
            matchedCount,
            targetSha256,
            verification: output.verification,
          },
          durationMs: Date.now() - started,
        };
      } catch (err) {
        if (err instanceof InvalidMemoryPathError) {
          return {
            status: "error",
            errorCode: "invalid_memory_path",
            errorMessage: err.message,
            durationMs: Date.now() - started,
          };
        }
        if (isFsSafeEscape(err)) {
          return {
            status: "error",
            errorCode: "path_escape",
            errorMessage: `path escape detected: ${(err as Error).message}`,
            durationMs: Date.now() - started,
          };
        }
        return errorResult(err, started);
      }
    },
  };
}

class InvalidMemoryPathError extends Error {
  constructor(relPath: string) {
    super(`MemoryRedact may only edit memory files, got ${relPath}`);
    this.name = "InvalidMemoryPathError";
  }
}

function sha256(value: string): string {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function countOccurrences(content: string, needle: string): number {
  if (needle.length === 0) return 0;
  let count = 0;
  let offset = 0;
  for (;;) {
    const next = content.indexOf(needle, offset);
    if (next < 0) return count;
    count += 1;
    offset = next + needle.length;
  }
}

function normalizeExplicitPaths(paths: readonly string[]): string[] {
  return [...new Set(paths.map((item) => normalizeMemoryPath(item)))];
}

function normalizeMemoryPath(input: string): string {
  const posix = input.replace(/\\/g, "/").replace(/^\.\/+/, "");
  if (path.posix.isAbsolute(posix)) throw new InvalidMemoryPathError(input);
  const normalized = path.posix.normalize(posix);
  if (normalized === "." || normalized.startsWith("../") || normalized.includes("/../")) {
    throw new InvalidMemoryPathError(input);
  }
  if (normalized === "MEMORY.md") return normalized;
  if (!normalized.startsWith("memory/")) throw new InvalidMemoryPathError(input);
  if (normalized.startsWith("memory/.redactions/")) throw new InvalidMemoryPathError(input);
  if (path.posix.basename(normalized).startsWith(".")) throw new InvalidMemoryPathError(input);
  return normalized;
}

async function discoverMemoryFiles(workspaceRoot: string): Promise<string[]> {
  const discovered: string[] = [];
  const memoryRoot = path.join(workspaceRoot, "memory");
  await walkMemoryDir(memoryRoot, "memory", discovered).catch((err) => {
    const code = (err as NodeJS.ErrnoException).code;
    if (code !== "ENOENT") throw err;
  });
  try {
    const stat = await fs.stat(path.join(workspaceRoot, "MEMORY.md"));
    if (stat.isFile()) discovered.push("MEMORY.md");
  } catch {
    // optional legacy file
  }
  return discovered.sort();
}

async function walkMemoryDir(
  fullDir: string,
  relDir: string,
  out: string[],
): Promise<void> {
  const entries = await fs.readdir(fullDir, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.name.startsWith(".")) continue;
    const rel = path.posix.join(relDir, entry.name);
    const full = path.join(fullDir, entry.name);
    if (entry.isDirectory()) {
      await walkMemoryDir(full, rel, out);
      continue;
    }
    if (entry.isFile() && isMemoryTextFile(entry.name)) {
      out.push(normalizeMemoryPath(rel));
    }
  }
}

function isMemoryTextFile(name: string): boolean {
  return /\.(?:md|txt|jsonl)$/i.test(name);
}

async function appendAuditRecord(input: {
  workspaceRoot: string;
  now: Date;
  record: Record<string, unknown>;
}): Promise<string> {
  const day = input.now.toISOString().slice(0, 10);
  const relPath = `memory/.redactions/${day}.jsonl`;
  const fullPath = path.join(input.workspaceRoot, relPath);
  await fs.mkdir(path.dirname(fullPath), { recursive: true });
  await fs.appendFile(fullPath, `${JSON.stringify(input.record)}\n`, "utf8");
  return relPath;
}
