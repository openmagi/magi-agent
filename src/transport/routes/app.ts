import fs from "node:fs/promises";
import path from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";
import { parseUrl, route, type HttpServerCtx, type RouteHandler } from "./_helpers.js";

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".webmanifest": "application/manifest+json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
};

function candidateAppRoots(): string[] {
  return [
    ...(process.env.MAGI_AGENT_APP_ROOT ? [process.env.MAGI_AGENT_APP_ROOT] : []),
    path.resolve(process.cwd(), "apps/web/dist"),
    path.resolve(__dirname, "../../../apps/web/dist"),
    path.resolve(__dirname, "../../../../apps/web/dist"),
  ];
}

async function findAppRoot(): Promise<string | null> {
  for (const candidate of candidateAppRoots()) {
    try {
      const stat = await fs.stat(path.join(candidate, "index.html"));
      if (stat.isFile()) return candidate;
    } catch {
      /* try the next candidate */
    }
  }
  return null;
}

function requestedAsset(req: IncomingMessage): string | null {
  const url = parseUrl(req.url);
  const pathname = url.pathname;
  if (pathname === "/dashboard" || pathname.startsWith("/dashboard/")) {
    return "index.html";
  }
  if (pathname === "/app" || pathname === "/app/") {
    return "index.html";
  }
  if (!pathname.startsWith("/app/")) {
    return null;
  }
  let decoded: string;
  try {
    decoded = decodeURIComponent(pathname.slice("/app/".length));
  } catch {
    return null;
  }
  const normalized = path.posix.normalize(`/${decoded}`).slice(1);
  if (
    normalized.length === 0 ||
    normalized === ".." ||
    normalized.startsWith("../") ||
    path.isAbsolute(normalized)
  ) {
    return null;
  }
  return normalized;
}

function writeText(res: ServerResponse, status: number, body: string): void {
  res.writeHead(status, {
    "Content-Type": "text/plain; charset=utf-8",
    "Cache-Control": "no-cache",
  });
  res.end(body);
}

function writeJson(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(body));
}

function isLoopbackAddress(address?: string): boolean {
  if (!address) return false;
  return address === "127.0.0.1" || address === "::1" || address === "::ffff:127.0.0.1";
}

function requestOrigin(req: IncomingMessage): string {
  const host = req.headers.host || "localhost";
  const forwardedProto = Array.isArray(req.headers["x-forwarded-proto"])
    ? req.headers["x-forwarded-proto"][0]
    : req.headers["x-forwarded-proto"];
  const proto = forwardedProto || "http";
  return `${proto}://${host}`;
}

async function handleBootstrap(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  const isLoopback = isLoopbackAddress(req.socket.remoteAddress);
  writeJson(res, 200, {
    ok: true,
    agentUrl: requestOrigin(req),
    tokenRequired: Boolean(ctx.bearerToken),
    ...(isLoopback && ctx.bearerToken ? { token: ctx.bearerToken } : {}),
  });
}

async function handleApp(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  _ctx: HttpServerCtx,
): Promise<void> {
  const root = await findAppRoot();
  const asset = requestedAsset(req);
  if (!root || !asset) {
    writeText(res, 404, "not found");
    return;
  }

  const target = path.resolve(root, asset);
  const rootWithSep = root.endsWith(path.sep) ? root : `${root}${path.sep}`;
  if (target !== root && !target.startsWith(rootWithSep)) {
    writeText(res, 404, "not found");
    return;
  }

  let body: Buffer;
  try {
    body = await fs.readFile(target);
  } catch {
    writeText(res, 404, "not found");
    return;
  }

  res.writeHead(200, {
    "Content-Type": MIME_TYPES[path.extname(target)] ?? "application/octet-stream",
    "Cache-Control": "no-cache",
  });
  res.end(body);
}

export const appRoutes: RouteHandler[] = [
  route("GET", /^\/app\/bootstrap\.json(?:\?.*)?$/, handleBootstrap),
  route("GET", /^\/app(?:\/[^?]*)?(?:\?.*)?$/, handleApp),
  route("GET", /^\/dashboard(?:\/[^?]*)?(?:\?.*)?$/, handleApp),
];
