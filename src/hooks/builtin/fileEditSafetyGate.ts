import crypto from "node:crypto";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import { isFsSafeEscape, readSafe } from "../../util/fsSafe.js";
import type { HookContext, RegisteredHook } from "../types.js";

export interface FileEditSafetyGateAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

export interface FileEditSafetyGateOptions {
  workspaceRoot: string;
  agent?: FileEditSafetyGateAgent;
}

interface PriorRead {
  path: string;
  hash: string;
}

async function readTranscript(
  opts: FileEditSafetyGateOptions,
  ctx: HookContext,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!opts.agent) return ctx.transcript;
  try {
    return (await opts.agent.readSessionTranscript(ctx.sessionKey)) ?? ctx.transcript;
  } catch (err) {
    ctx.log("warn", "[file-edit-safety] transcript read failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript;
  }
}

function pathFromInput(input: unknown): string | null {
  if (!input || typeof input !== "object") return null;
  const pathValue = (input as { path?: unknown }).path;
  return typeof pathValue === "string" && pathValue.length > 0 ? pathValue : null;
}

function parseHashFromOutput(output: string | undefined): string | null {
  if (!output) return null;
  try {
    const parsed = JSON.parse(output) as { fileSha256?: unknown; contentSha256?: unknown };
    if (typeof parsed.fileSha256 === "string" && parsed.fileSha256.length > 0) {
      return parsed.fileSha256;
    }
    if (typeof parsed.contentSha256 === "string" && parsed.contentSha256.length > 0) {
      return parsed.contentSha256;
    }
  } catch {
    const direct = /"(?:fileSha256|contentSha256)"\s*:\s*"([a-f0-9]{64})"/i.exec(output);
    return direct?.[1] ?? null;
  }
  return null;
}

function currentTurnFileReads(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): PriorRead[] {
  const results = new Map<string, Extract<TranscriptEntry, { kind: "tool_result" }>>();
  for (const entry of transcript) {
    if (entry.kind === "tool_result" && entry.turnId === turnId) {
      results.set(entry.toolUseId, entry);
    }
  }

  const reads: PriorRead[] = [];
  for (const entry of transcript) {
    if (entry.kind !== "tool_call" || entry.turnId !== turnId || entry.name !== "FileRead") {
      continue;
    }
    const result = results.get(entry.toolUseId);
    if (!result || result.isError === true || result.status !== "ok") continue;
    const pathValue = pathFromInput(entry.input);
    const hash = parseHashFromOutput(result.output);
    if (pathValue && hash) reads.push({ path: pathValue, hash });
  }
  return reads;
}

export function makeFileEditSafetyGateHook(
  opts: FileEditSafetyGateOptions,
): RegisteredHook<"beforeToolUse"> {
  return {
    name: "builtin:file-edit-safety-gate",
    point: "beforeToolUse",
    priority: 44,
    blocking: true,
    timeoutMs: 1_000,
    handler: async ({ toolName, input }, ctx) => {
      if (toolName !== "FileEdit") return { action: "continue" };
      const target = pathFromInput(input);
      if (!target) return { action: "continue" };

      const transcript = await readTranscript(opts, ctx);
      const prior = currentTurnFileReads(transcript, ctx.turnId)
        .reverse()
        .find((read) => read.path === target);
      if (!prior) {
        return {
          action: "block",
          reason: [
            "[RETRY:FILE_EDIT_PRIOR_READ] FileEdit requires a current-turn FileRead of the target file before editing.",
            `Read ${target} first, then retry the edit with fresh context.`,
          ].join("\n"),
        };
      }

      try {
        const current = await readSafe(target, opts.workspaceRoot);
        const currentHash = crypto.createHash("sha256").update(current).digest("hex");
        if (currentHash !== prior.hash) {
          return {
            action: "block",
            reason: [
              "[RETRY:FILE_EDIT_STALE_READ] FileEdit is stale: the file changed after the last FileRead evidence.",
              `Re-read ${target}, inspect the latest content, then retry the edit.`,
            ].join("\n"),
          };
        }
      } catch (err) {
        if (isFsSafeEscape(err)) {
          return {
            action: "block",
            reason: `FileEdit path escape detected: ${(err as Error).message}`,
          };
        }
        ctx.log("warn", "[file-edit-safety] current file read failed", {
          path: target,
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }

      return { action: "continue" };
    },
  };
}
