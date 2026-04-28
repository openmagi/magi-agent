/**
 * Shared helpers for per-domain route modules under `transport/routes/`.
 *
 * The route split (R5 — docs/plans/2026-04-19-core-agent-refactor-plan.md)
 * keeps the wire behaviour identical to the pre-split HttpServer.ts, so
 * every helper here preserves the exact status codes, response body
 * shapes, header names, and error strings used before.
 */

import { URL } from "node:url";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Agent } from "../../Agent.js";
import { safeCompare } from "../../util/safeCompare.js";

/**
 * Shared context passed to every RouteHandler. Keeps the bearer token
 * + agent reference in one place so individual route modules stay lean.
 */
export interface HttpServerCtx {
  readonly agent: Agent;
  readonly bearerToken?: string;
}

/**
 * Route match contract. Each domain module (health, turns, ...) exports
 * a flat `RouteHandler[]`. The HttpServer harness walks these in order
 * and invokes the first handler whose `match()` returns a non-null
 * value, propagating the match result into `handle()`.
 */
export interface RouteHandler {
  /**
   * Cheap pre-filter. Returning non-null indicates a route hit; the
   * returned value is forwarded to `handle()` so route modules can
   * capture path params (e.g. `:turnId`) without re-running the regex.
   */
  match(req: IncomingMessage, url: string): RegExpMatchArray | boolean | null;
  handle(
    req: IncomingMessage,
    res: ServerResponse,
    match: RegExpMatchArray | boolean,
    ctx: HttpServerCtx,
  ): Promise<void>;
}

/**
 * Build a method+regex RouteHandler. The regex is matched against the
 * request URL (path + optional query). Returns the RegExpMatchArray so
 * downstream handlers can extract path params.
 */
export function route(
  method: string,
  pattern: RegExp,
  handle: (
    req: IncomingMessage,
    res: ServerResponse,
    match: RegExpMatchArray,
    ctx: HttpServerCtx,
  ) => Promise<void>,
): RouteHandler {
  return {
    match(req, url) {
      if (req.method !== method) return null;
      return url.match(pattern);
    },
    async handle(req, res, match, ctx) {
      await handle(req, res, match as RegExpMatchArray, ctx);
    },
  };
}

/**
 * Build a RouteHandler that matches a URL prefix. Used for gateways
 * like `/v1/contexts` that dispatch internally by method + subpath.
 */
export function prefixRoute(
  prefix: string,
  handle: (
    req: IncomingMessage,
    res: ServerResponse,
    ctx: HttpServerCtx,
  ) => Promise<void>,
): RouteHandler {
  return {
    match(_req, url) {
      return url.startsWith(prefix);
    },
    async handle(req, res, _match, ctx) {
      await handle(req, res, ctx);
    },
  };
}

/**
 * Constant-time compare of `X-Gateway-Token` to the configured bearer
 * token. Writes 401 on mismatch and returns false. Behaviour matches
 * the pre-split HttpServer.authorizeGateway exactly.
 */
export function authorizeGateway(
  req: IncomingMessage,
  res: ServerResponse,
  ctx: HttpServerCtx,
): boolean {
  if (!ctx.bearerToken) {
    res.writeHead(401, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "unauthorized" }));
    return false;
  }
  const provided = (req.headers["x-gateway-token"] as string | undefined) ?? "";
  if (!provided || !safeCompare(provided, ctx.bearerToken)) {
    res.writeHead(401, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "unauthorized" }));
    return false;
  }
  return true;
}

/**
 * Verify `Authorization: Bearer <token>`. Used by legacy endpoints
 * (chat completions, ask-response) that pre-date the gateway-token
 * scheme. Writes 401 on mismatch and returns false.
 */
export function authorizeBearer(
  req: IncomingMessage,
  res: ServerResponse,
  ctx: HttpServerCtx,
): boolean {
  if (!ctx.bearerToken) return true;
  const auth = req.headers.authorization ?? "";
  const expected = `Bearer ${ctx.bearerToken}`;
  if (auth !== expected) {
    res.writeHead(401, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "unauthorized" }));
    return false;
  }
  return true;
}

/** Parse a request URL; host is irrelevant. */
export function parseUrl(reqUrl: string | undefined): URL {
  return new URL(reqUrl ?? "/", "http://localhost");
}

/** Parse an integer query param, returning undefined on missing/invalid. */
export function numberParam(raw: string | null): number | undefined {
  if (raw === null || raw === "") return undefined;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) ? n : undefined;
}

/** Clamp a numeric query param into [min, max]. */
export function clampLimit(
  raw: string | null,
  min: number,
  max: number,
  fallback: number,
): number {
  if (raw === null) return fallback;
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n)) return fallback;
  if (n < min) return min;
  if (n > max) return max;
  return n;
}

/**
 * Read the request body as JSON. Streaming variant with a 20 MB cap,
 * matching the chat-proxy upload limit. Rejects on body overflow.
 */
export function readJsonBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let total = 0;
    const LIMIT = 20 * 1024 * 1024; // 20 MB — matches chat-proxy cap
    req.on("data", (chunk: Buffer) => {
      total += chunk.length;
      if (total > LIMIT) {
        req.destroy();
        reject(new Error(`body too large (>${LIMIT} bytes)`));
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      try {
        const text = Buffer.concat(chunks).toString("utf8");
        resolve(text.length > 0 ? JSON.parse(text) : {});
      } catch (err) {
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    });
    req.on("error", reject);
  });
}

/**
 * Tolerant JSON body reader: returns `{}` on parse errors or non-object
 * payloads. Used by routes that want to proceed with optional body
 * fields even when the body is malformed or empty.
 */
export async function readJsonBodyLenient(
  req: IncomingMessage,
): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const c of req) {
    chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c));
  }
  const raw = Buffer.concat(chunks).toString("utf8");
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

/** Convenience: write a JSON response with the given status + body. */
export function writeJson(
  res: ServerResponse,
  status: number,
  body: unknown,
): void {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}
