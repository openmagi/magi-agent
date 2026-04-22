/**
 * Built-in hipocampus flush hook — pre-compaction transcript drain.
 *
 * Before the compaction engine runs, this hook extracts unflushed
 * user/assistant turns from the transcript and appends them to the
 * daily memory log (`memory/YYYY-MM-DD.md`). A marker file
 * (`memory/.last-flushed-turn`) tracks the last flushed turnId to
 * prevent duplicates across repeated compaction cycles.
 *
 * Also exports a standalone `flushMemory()` for the /reset handler
 * (not hook-triggered).
 */

import fs from "node:fs/promises";
import path from "node:path";
import type { RegisteredHook } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";

const MARKER_FILE = ".last-flushed-turn";

function fmtDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n).trimEnd() + "…";
}

/**
 * Extract user_message and assistant_text entries from a transcript,
 * filtering to only those after `lastFlushedTurnId` (if provided).
 * Returns entries grouped by turnId with deduplication.
 */
function extractFlushableEntries(
  transcript: ReadonlyArray<TranscriptEntry>,
  lastFlushedTurnId: string | null,
): Array<{ kind: "user_message" | "assistant_text"; turnId: string; ts: number; text: string }> {
  let pastMarker = lastFlushedTurnId === null;
  const result: Array<{ kind: "user_message" | "assistant_text"; turnId: string; ts: number; text: string }> = [];

  for (const entry of transcript) {
    if (!pastMarker) {
      if ("turnId" in entry && entry.turnId === lastFlushedTurnId) {
        pastMarker = true;
      }
      continue;
    }

    // Skip remaining entries that share the marker turnId
    if ("turnId" in entry && entry.turnId === lastFlushedTurnId) {
      continue;
    }

    if (entry.kind === "user_message" || entry.kind === "assistant_text") {
      result.push({
        kind: entry.kind,
        turnId: entry.turnId,
        ts: entry.ts,
        text: entry.text,
      });
    }
  }

  return result;
}

/**
 * Format extracted entries into a markdown log block.
 */
function formatEntries(
  entries: Array<{ kind: "user_message" | "assistant_text"; turnId: string; ts: number; text: string }>,
): string {
  if (entries.length === 0) return "";

  const lines: string[] = [];
  let currentTurnId: string | null = null;

  for (const entry of entries) {
    if (entry.turnId !== currentTurnId) {
      currentTurnId = entry.turnId;
      lines.push("");
      lines.push(`## ${new Date(entry.ts).toISOString()} · ${entry.turnId}`);
      lines.push("");
    }

    const role = entry.kind === "user_message" ? "User" : "Assistant";
    lines.push(`**${role}:** ${truncate(entry.text.replace(/\s+/g, " "), 600)}`);
    lines.push("");
  }

  lines.push("---");
  lines.push("");

  return lines.join("\n");
}

export interface FlushDeps {
  readFile: (p: string, encoding: BufferEncoding) => Promise<string>;
  writeFile: (p: string, data: string, encoding: BufferEncoding) => Promise<void>;
  appendFile: (p: string, data: string, encoding: BufferEncoding) => Promise<void>;
  mkdir: (p: string, opts: { recursive: boolean }) => Promise<string | undefined>;
}

const defaultDeps: FlushDeps = {
  readFile: (p, enc) => fs.readFile(p, enc),
  writeFile: (p, data, enc) => fs.writeFile(p, data, enc),
  appendFile: (p, data, enc) => fs.appendFile(p, data, enc),
  mkdir: (p, opts) => fs.mkdir(p, opts),
};

/**
 * Standalone flush function for use by /reset handler and other
 * non-hook callers.
 */
export async function flushMemory(
  workspaceRoot: string,
  transcript: ReadonlyArray<TranscriptEntry>,
  deps: FlushDeps = defaultDeps,
): Promise<{ flushed: number; lastTurnId: string | null }> {
  const memoryDir = path.join(workspaceRoot, "memory");
  const markerPath = path.join(memoryDir, MARKER_FILE);

  // Read the last flushed turnId
  let lastFlushedTurnId: string | null = null;
  try {
    lastFlushedTurnId = (await deps.readFile(markerPath, "utf8")).trim() || null;
  } catch {
    // No marker file yet — flush everything
  }

  const entries = extractFlushableEntries(transcript, lastFlushedTurnId);
  if (entries.length === 0) {
    return { flushed: 0, lastTurnId: lastFlushedTurnId };
  }

  const formatted = formatEntries(entries);
  const lastEntry = entries[entries.length - 1]!;
  const dateStr = fmtDate(new Date(lastEntry.ts));
  const logPath = path.join(memoryDir, `${dateStr}.md`);

  await deps.mkdir(memoryDir, { recursive: true });
  await deps.appendFile(logPath, formatted, "utf8");

  // Update marker with the last turnId we flushed
  const newLastTurnId = lastEntry.turnId;
  await deps.writeFile(markerPath, newLastTurnId, "utf8");

  return { flushed: entries.length, lastTurnId: newLastTurnId };
}

export function makeHipocampusFlushHook(
  workspaceRoot: string,
  deps: FlushDeps = defaultDeps,
): RegisteredHook<"beforeCompaction"> {
  return {
    name: "builtin:hipocampus-flush",
    point: "beforeCompaction",
    priority: 1,
    blocking: true,
    timeoutMs: 5_000,
    handler: async (args, ctx) => {
      try {
        const { flushed, lastTurnId } = await flushMemory(
          workspaceRoot,
          args.transcript,
          deps,
        );
        ctx.log("info", "hipocampus flush completed", {
          flushed,
          lastTurnId,
        });
      } catch (err) {
        ctx.log("warn", "hipocampus flush failed", {
          error: String(err),
        });
      }
    },
  };
}
