/**
 * Session introspection routes. All gateway-token gated. Behaviour
 * preserved verbatim from the pre-split HttpServer.ts.
 *
 *   GET /v1/session/:sessionKey/stats
 *     T1-06 budget snapshot: turn/cost counts + maxTurns/maxCostUsd.
 *
 *   GET /v1/session/:sessionKey/permission
 *     T2-08 permission-mode snapshot: mode, prePlanMode, isPlanMode.
 *
 *   POST /v1/sessions/:sessionKey/rollback
 *     Rollback workspace to a specific turn or SHA. Rejects 409 if
 *     a turn is in progress.
 */

import {
  authorizeGateway,
  readJsonBodyLenient,
  route,
  writeJson,
  type HttpServerCtx,
  type RouteHandler,
} from "./_helpers.js";
import type { IncomingMessage, ServerResponse } from "node:http";

async function handleSessionStats(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeGateway(req, res, ctx)) return;
  const sessionKey = decodeURIComponent(match[1] as string);
  const session = ctx.agent
    .listSessions()
    .find((s) => s.meta.sessionKey === sessionKey);
  if (!session) {
    writeJson(res, 404, { error: "not_found" });
    return;
  }
  const stats = session.budgetStats();
  writeJson(res, 200, {
    sessionKey,
    botId: ctx.agent.config.botId,
    ...stats,
    maxTurns: session.maxTurns,
    maxCostUsd: session.maxCostUsd,
  });
}

async function handleSessionPermission(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeGateway(req, res, ctx)) return;
  const sessionKey = decodeURIComponent(match[1] as string);
  const session = ctx.agent
    .listSessions()
    .find((s) => s.meta.sessionKey === sessionKey);
  if (!session) {
    writeJson(res, 404, { error: "not_found" });
    return;
  }
  const mode = session.getPermissionMode();
  const prePlanMode = session.getPrePlanMode();
  writeJson(res, 200, {
    sessionKey,
    botId: ctx.agent.config.botId,
    mode,
    prePlanMode,
    isPlanMode: mode === "plan",
  });
}

async function handleSessionRollback(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeGateway(req, res, ctx)) return;
  const sessionKey = decodeURIComponent(match[1] as string);

  if (ctx.agent.hasActiveTurnForSession(sessionKey)) {
    writeJson(res, 409, {
      error: "turn_in_progress",
      message: "Cannot rollback while a turn is active",
    });
    return;
  }

  const body = await readJsonBodyLenient(req);
  const turnId = typeof body["turnId"] === "string" ? body["turnId"] : undefined;
  const sha = typeof body["sha"] === "string" ? body["sha"] : undefined;

  if (!turnId && !sha) {
    writeJson(res, 400, {
      error: "missing_target",
      message: "Provide turnId or sha",
    });
    return;
  }

  const snapshotService = ctx.agent.getTurnSnapshotService?.();
  if (!snapshotService) {
    writeJson(res, 501, {
      error: "not_enabled",
      message: "Turn snapshots not enabled (MAGI_TURN_SNAPSHOT=1)",
    });
    return;
  }

  try {
    let result: { restoredSha: string; restoredFiles: string[] };
    if (turnId) {
      const r = await snapshotService.rollbackTurn(turnId);
      if (!r) {
        writeJson(res, 404, { error: "snapshot_not_found" });
        return;
      }
      result = r;
    } else {
      result = await snapshotService.rollbackToSha(sha!);
    }
    writeJson(res, 200, {
      restored: true,
      sha: result.restoredSha,
      filesRestored: result.restoredFiles,
    });
  } catch (err) {
    writeJson(res, 500, {
      error: "rollback_failed",
      message: String(err),
    });
  }
}

export const sessionRoutes: RouteHandler[] = [
  route(
    "GET",
    /^\/v1\/session\/([^/?]+)\/stats(?:\?.*)?$/,
    handleSessionStats,
  ),
  route(
    "GET",
    /^\/v1\/session\/([^/?]+)\/permission(?:\?.*)?$/,
    handleSessionPermission,
  ),
  route(
    "POST",
    /^\/v1\/sessions\/([^/?]+)\/rollback(?:\?.*)?$/,
    handleSessionRollback,
  ),
];
