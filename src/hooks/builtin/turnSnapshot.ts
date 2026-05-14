/**
 * builtin:turn-snapshot-start / builtin:turn-snapshot-end — per-turn
 * boundary snapshots using ShadowGit.
 *
 * Non-blocking (blocking: false, priority: 95) — snapshot failure
 * never aborts a turn. Gated by MAGI_TURN_SNAPSHOT env var.
 */

import type { RegisteredHook } from "../types.js";
import { ShadowGit } from "../../checkpoint/ShadowGit.js";
import { TurnSnapshotService } from "../../checkpoint/TurnSnapshotService.js";

export interface TurnSnapshotHookOptions {
  workspaceRoot: string;
  enabled?: boolean;
}

export interface TurnSnapshotHooks {
  start: RegisteredHook<"beforeTurnStart">;
  end: RegisteredHook<"afterTurnEnd">;
  service: TurnSnapshotService;
}

export function makeTurnSnapshotHooks(
  opts: TurnSnapshotHookOptions,
): TurnSnapshotHooks {
  const enabled =
    opts.enabled ??
    (process.env["MAGI_TURN_SNAPSHOT"] === "1");

  const shadowGit = new ShadowGit({ workspaceRoot: opts.workspaceRoot });
  const service = new TurnSnapshotService(shadowGit);

  const start: RegisteredHook<"beforeTurnStart"> = {
    name: "builtin:turn-snapshot-start",
    point: "beforeTurnStart",
    priority: 95,
    blocking: false,
    timeoutMs: 5_000,
    handler: async (_args, ctx) => {
      if (!enabled) return;
      try {
        const sha = await service.snapshotTurnStart(ctx.turnId, ctx.sessionKey);
        if (sha) {
          ctx.log("info", "turn snapshot start", {
            sha: sha.slice(0, 8),
            turnId: ctx.turnId,
          });
        }
      } catch (err) {
        ctx.log("warn", "turn snapshot start failed", {
          error: String(err),
          turnId: ctx.turnId,
        });
      }
    },
  };

  const end: RegisteredHook<"afterTurnEnd"> = {
    name: "builtin:turn-snapshot-end",
    point: "afterTurnEnd",
    priority: 95,
    blocking: false,
    timeoutMs: 5_000,
    handler: async (_args, ctx) => {
      if (!enabled) return;
      try {
        const startSha = service.getStartSha(ctx.turnId);
        const snap = await service.snapshotTurnEnd(
          ctx.turnId,
          ctx.sessionKey,
          startSha ?? null,
        );
        if (snap) {
          ctx.log("info", "turn snapshot end", {
            startSha: snap.startSha.slice(0, 8),
            endSha: snap.endSha.slice(0, 8),
            filesChanged: snap.filesChanged.length,
            patchTruncated: snap.patchTruncated,
            turnId: ctx.turnId,
          });
        }
      } catch (err) {
        ctx.log("warn", "turn snapshot end failed", {
          error: String(err),
          turnId: ctx.turnId,
        });
      }
    },
  };

  return { start, end, service };
}
