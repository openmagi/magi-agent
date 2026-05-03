/**
 * Built-in hipocampus compactor hook — session-first-turn compaction.
 *
 * On the first turn of each session, flushes the transcript to daily
 * memory logs, then triggers the compaction engine to roll up
 * daily → weekly → monthly → root, then reindexes qmd so search
 * stays fresh. Fail-open: errors are logged but never block the turn.
 */

import type { RegisteredHook } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import { flushMemory as defaultFlushMemory } from "./hipocampusFlush.js";
import {
  allowSealedFileUpdateForTurn,
  recordSystemSealedFileUpdate,
} from "./sealedFiles.js";

export interface CompactionEngine {
  run: (force?: boolean) => Promise<{ skipped?: boolean; compacted?: boolean; stats?: unknown }>;
}

export interface QmdManager {
  reindex: () => Promise<void>;
}

export type FlushFn = (
  workspaceRoot: string,
  transcript: ReadonlyArray<TranscriptEntry>,
) => Promise<{ flushed: number; lastTurnId: string | null }>;

export function makeHipocampusCompactorHook(
  engine: CompactionEngine,
  qmd: QmdManager,
  flush: FlushFn = defaultFlushMemory,
  workspaceRoot?: string,
): RegisteredHook<"beforeTurnStart"> {
  const seenSessions = new Set<string>();

  return {
    name: "builtin:hipocampus-compactor",
    point: "beforeTurnStart",
    priority: 99,
    blocking: false,
    timeoutMs: 30_000,
    handler: async (_args, ctx) => {
      const sessionKey = ctx.sessionKey;

      if (seenSessions.has(sessionKey)) {
        ctx.log("info", "hipocampus compactor: session already seen, skipping", {
          sessionKey,
        });
        return;
      }

      seenSessions.add(sessionKey);

      // Flush transcript → memory/YYYY-MM-DD.md before compaction
      try {
        const wsRoot = workspaceRoot ?? "";
        const { flushed } = await flush(wsRoot, ctx.transcript);
        ctx.log("info", "hipocampus flush before compaction", {
          sessionKey,
          flushed,
        });
      } catch (flushErr) {
        ctx.log("warn", "hipocampus flush before compaction failed", {
          sessionKey,
          error: String(flushErr),
        });
        // Continue — compaction can still process existing files
      }

      try {
        const result = await engine.run();

        ctx.log("info", "hipocampus compaction completed", {
          sessionKey,
          skipped: result.skipped ?? false,
          compacted: result.compacted ?? false,
        });

        if (result.compacted) {
          if (workspaceRoot) {
            try {
              const recorded = await recordSystemSealedFileUpdate(
                workspaceRoot,
                "memory/ROOT.md",
              );
              ctx.log("info", "hipocampus sealed manifest update completed", {
                sessionKey,
                recorded,
              });
            } catch (manifestErr) {
              ctx.log("warn", "hipocampus sealed manifest update failed", {
                sessionKey,
                error: String(manifestErr),
              });
            }
          } else {
            allowSealedFileUpdateForTurn(ctx.turnId, "memory/ROOT.md");
          }
          try {
            await qmd.reindex();
            ctx.log("info", "hipocampus qmd reindex completed", { sessionKey });
          } catch (reindexErr) {
            ctx.log("warn", "hipocampus qmd reindex failed", {
              error: String(reindexErr),
            });
          }
        }
      } catch (err) {
        ctx.log("warn", "hipocampus compaction failed", {
          sessionKey,
          error: String(err),
        });
      }
    },
  };
}
