import type { Agent } from "../../Agent.js";
import type { ControlEvent } from "../../control/ControlEvents.js";
import { buildParityEvidenceReport } from "../../parity/ParityEvidence.js";
import {
  authorizeBearer,
  parseUrl,
  route,
  writeJson,
  type HttpServerCtx,
  type RouteHandler,
} from "./_helpers.js";
import { buildHealthPayloadForAgent } from "./health.js";

interface SessionWithControlEvents {
  meta: { sessionKey: string };
  controlEvents?: {
    readAll?: () => Promise<ControlEvent[]>;
  };
}

export const parityRoutes: RouteHandler[] = [
  route("GET", /^\/v1\/parity\/evidence(?:\?.*)?$/, handleParityEvidence),
];

async function handleParityEvidence(
  req: Parameters<RouteHandler["handle"]>[0],
  res: Parameters<RouteHandler["handle"]>[1],
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;

  const url = parseUrl(req.url);
  const requestedSessionKey = url.searchParams.get("sessionKey")?.trim() ?? "";
  const includeEvents = url.searchParams.get("includeEvents") === "1";
  if (!requestedSessionKey) {
    writeJson(res, 400, { error: "session_key_required" });
    return;
  }

  const sessions = selectSessions(ctx.agent, requestedSessionKey);
  if (sessions.length === 0) {
    writeJson(res, 404, { error: "session_not_found" });
    return;
  }

  const sessionReports: Array<{
    sessionKey: string;
    eventCount: number;
    lastSeq: number;
    events?: ControlEvent[];
  }> = [];
  const allEvents: ControlEvent[] = [];
  for (const session of sessions) {
    const events = await readSessionEvents(session);
    allEvents.push(...events);
    sessionReports.push({
      sessionKey: session.meta.sessionKey,
      eventCount: events.length,
      lastSeq: events.reduce((max, event) => Math.max(max, event.seq), 0),
      ...(includeEvents ? { events } : {}),
    });
  }

  allEvents.sort((a, b) => a.seq - b.seq || a.ts - b.ts);
  const health = await buildHealthPayloadForAgent(ctx.agent);
  const report = buildParityEvidenceReport({
    runtime: {
      ok: health.ok,
      degradedReasons: health.degradedReasons,
      buildInfo: health,
      features: health.features,
      skills: health.skills,
    },
    controlEvents: allEvents,
  });

  writeJson(res, 200, {
    ok: true,
    ready: report.ready,
    report,
    sessions: sessionReports,
  });
}

function selectSessions(agent: Agent, requestedSessionKey: string): SessionWithControlEvents[] {
  const session = agent.getSession(requestedSessionKey) as SessionWithControlEvents | undefined;
  return session ? [session] : [];
}

async function readSessionEvents(
  session: SessionWithControlEvents,
): Promise<ControlEvent[]> {
  const readAll = session.controlEvents?.readAll;
  if (typeof readAll !== "function") return [];
  return await readAll.call(session.controlEvents);
}
