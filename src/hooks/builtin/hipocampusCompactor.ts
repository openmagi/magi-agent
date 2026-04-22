/**
 * Built-in hipocampus compactor hook — session-first-turn compaction.
 *
 * On the first turn of each session, triggers the compaction engine
 * to roll up daily → weekly → monthly → root memory logs, then
 * reindexes qmd so search stays fresh. Fail-open: errors are logged
 * but never block the turn.
 */

import type { RegisteredHook } from "../types.js";

export interface CompactionEngine {
  run: (force?: boolean) => Promise<{ skipped?: boolean; compacted?: boolean; stats?: unknown }>;
}

export interface QmdManager {
  reindex: () => Promise<void>;
}

export function makeHipocampusCompactorHook(
  engine: CompactionEngine,
  qmd: QmdManager,
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

      try {
        const result = await engine.run();

        ctx.log("info", "hipocampus compaction completed", {
          sessionKey,
          skipped: result.skipped ?? false,
          compacted: result.compacted ?? false,
        });

        if (result.compacted) {
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
