/**
 * HTTP ingress on :8080 — gateway-compatible surface.
 * Design reference: §9.6.
 *
 * This file is intentionally the thinnest possible harness around
 * `http.createServer`. Per-domain request handlers live under
 * `transport/routes/` and are declared as `RouteHandler` entries in
 * the flat dispatch table below (R5,
 * docs/plans/2026-04-19-core-agent-refactor-plan.md).
 *
 * Routes are walked top-to-bottom on every request; the first handler
 * whose `match()` returns a non-null value wins. Anything that falls
 * off the end returns 404 `{error:"not_found"}`.
 */

import http, {
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import type { Agent } from "../Agent.js";
import type { HttpServerCtx, RouteHandler } from "./routes/_helpers.js";
import { healthRoutes } from "./routes/health.js";
import { complianceRoutes } from "./routes/compliance.js";
import { sessionRoutes } from "./routes/session.js";
import { contextsRoutes } from "./routes/contexts.js";
import { turnsRoutes } from "./routes/turns.js";
import { mcpRoutes } from "./routes/mcp.js";
import { heartbeatRoutes } from "./routes/heartbeat.js";
import { parityRoutes } from "./routes/parity.js";

export interface HttpServerOptions {
  port: number;
  agent: Agent;
  /**
   * Optional auth gate. When set, POST /v1/chat/completions rejects
   * requests without a matching Bearer token. In prod this is the
   * gateway token from the per-bot Secret.
   */
  bearerToken?: string;
}

export class HttpServer {
  private server?: http.Server;
  private readonly port: number;
  private readonly agent: Agent;
  private readonly bearerToken?: string;

  constructor(opts: HttpServerOptions) {
    this.port = opts.port;
    this.agent = opts.agent;
    this.bearerToken = opts.bearerToken;
  }

  async start(): Promise<void> {
    this.server = http.createServer((req, res) => {
      this.handle(req, res).catch((err) => {
        console.error("[http] handler error", err);
        if (!res.headersSent) {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "internal", message: String(err) }));
        } else {
          res.end();
        }
      });
    });
    // Disable Node's default 5-min requestTimeout — SSE turns can run for
    // hours when orchestrating subagents or long tool chains. Chat-proxy
    // emits heartbeats every 15s and the client runs its own rolling idle
    // window, so socket liveness is enforced at the edges, not here.
    this.server.requestTimeout = 0;
    this.server.timeout = 0;
    this.server.headersTimeout = 120_000;
    this.server.keepAliveTimeout = 120_000;
    await new Promise<void>((resolve) => {
      this.server!.listen(this.port, resolve);
    });
  }

  async stop(): Promise<void> {
    if (!this.server) return;
    await new Promise<void>((resolve, reject) => {
      this.server!.close((err) => (err ? reject(err) : resolve()));
    });
    this.server = undefined;
  }

  /** Flat dispatch table; walked top-to-bottom. See routes/*.ts. */
  private get routes(): RouteHandler[] {
    return [
      ...healthRoutes,
      ...complianceRoutes,
      ...sessionRoutes,
      ...contextsRoutes,
      ...turnsRoutes,
      ...parityRoutes,
      ...mcpRoutes,
      ...heartbeatRoutes,
    ];
  }

  private get routeCtx(): HttpServerCtx {
    return this.bearerToken !== undefined
      ? { agent: this.agent, bearerToken: this.bearerToken }
      : { agent: this.agent };
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = req.url ?? "/";

    // Dispatch table first — per-domain handlers declared in routes/*.ts.
    for (const r of this.routes) {
      const m = r.match(req, url);
      if (m !== null && m !== false) {
        await r.handle(req, res, m, this.routeCtx);
        return;
      }
    }

    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "not_found" }));
  }
}
