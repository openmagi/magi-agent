import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs/promises";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import { withMagiBinPath } from "../../util/shellPath.js";
import {
  authorizeBearer,
  readJsonBody,
  route,
  writeJson,
  type HttpServerCtx,
  type RouteHandler,
} from "./_helpers.js";

type SocialProvider = "instagram" | "x";
type SocialCommandAction = "screenshot" | "navigate" | "click" | "type" | "key";

interface LocalSocialSession {
  provider: SocialProvider;
  sessionId: string;
  agentSessionName: string;
  createdAt: number;
  updatedAt: number;
  expiresAt: number;
}

interface AgentBrowserRun {
  exitCode: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
}

const SESSION_TTL_MS = 30 * 60 * 1000;
const COMMAND_TIMEOUT_MS = 30_000;
const SCREENSHOT_TIMEOUT_MS = 15_000;
const MAX_STDIO_BYTES = 64 * 1024;
const MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024;
const LOCAL_SOCIAL_SESSIONS = new Map<string, LocalSocialSession>();

export function resetLocalSocialBrowserSessionsForTests(): void {
  LOCAL_SOCIAL_SESSIONS.clear();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function providerFrom(value: unknown): SocialProvider | null {
  if (value === "instagram" || value === "ig") return "instagram";
  if (value === "x" || value === "twitter") return "x";
  return null;
}

function decodePathPart(raw: string): string | null {
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

function providerStartUrl(provider: SocialProvider): string {
  return provider === "instagram"
    ? "https://www.instagram.com/accounts/login/"
    : "https://x.com/i/flow/login";
}

function nowMs(): number {
  return Date.now();
}

function expireSessions(now = nowMs()): void {
  for (const [sessionId, session] of LOCAL_SOCIAL_SESSIONS.entries()) {
    if (session.expiresAt <= now) LOCAL_SOCIAL_SESSIONS.delete(sessionId);
  }
}

function publicSession(session: LocalSocialSession) {
  return {
    provider: session.provider,
    sessionId: session.sessionId,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
    expiresAt: session.expiresAt,
  };
}

function clip(value: string): string {
  return value.length > 300 ? `${value.slice(0, 297)}...` : value;
}

function appendChunk(chunks: Buffer[], chunk: Buffer): void {
  const current = chunks.reduce((sum, item) => sum + item.length, 0);
  if (current >= MAX_STDIO_BYTES) return;
  chunks.push(chunk.subarray(0, Math.max(0, MAX_STDIO_BYTES - current)));
}

function runAgentBrowser(
  ctx: HttpServerCtx,
  args: string[],
  timeoutMs = COMMAND_TIMEOUT_MS,
): Promise<AgentBrowserRun> {
  return new Promise((resolve, reject) => {
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    const child = spawn("agent-browser", args, {
      cwd: ctx.agent.config.workspaceRoot,
      env: {
        ...withMagiBinPath(process.env),
        PWD: ctx.agent.config.workspaceRoot,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
    }, timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => appendChunk(stdout, chunk));
    child.stderr.on("data", (chunk: Buffer) => appendChunk(stderr, chunk));
    child.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
    child.on("close", (exitCode, signal) => {
      clearTimeout(timer);
      resolve({
        exitCode,
        signal,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
      });
    });
  });
}

async function runChecked(
  ctx: HttpServerCtx,
  args: string[],
  timeoutMs = COMMAND_TIMEOUT_MS,
): Promise<AgentBrowserRun> {
  const run = await runAgentBrowser(ctx, args, timeoutMs);
  if (run.exitCode !== 0) {
    throw new Error(clip(run.stderr || run.stdout || "agent-browser command failed"));
  }
  return run;
}

function sessionName(provider: SocialProvider, sessionId: string): string {
  return `magi-app-social-${provider}-${sessionId}`;
}

function screenshotsDir(ctx: HttpServerCtx): string {
  return path.join(ctx.agent.config.workspaceRoot, ".magi", "app-social-browser");
}

function screenshotContentType(buffer: Buffer): "image/png" | "image/jpeg" {
  return buffer.length >= 3 &&
    buffer[0] === 0xff &&
    buffer[1] === 0xd8 &&
    buffer[2] === 0xff
    ? "image/jpeg"
    : "image/png";
}

async function captureScreenshot(ctx: HttpServerCtx, session: LocalSocialSession) {
  const dir = screenshotsDir(ctx);
  await fs.mkdir(dir, { recursive: true });
  const screenshotPath = path.join(dir, `${session.sessionId}.png`);
  await runChecked(
    ctx,
    ["--session", session.agentSessionName, "screenshot", screenshotPath],
    SCREENSHOT_TIMEOUT_MS,
  );
  const stat = await fs.stat(screenshotPath);
  if (!stat.isFile() || stat.size <= 0 || stat.size > MAX_SCREENSHOT_BYTES) {
    throw new Error("invalid browser screenshot");
  }
  const buffer = await fs.readFile(screenshotPath);
  const urlRun = await runAgentBrowser(
    ctx,
    ["--session", session.agentSessionName, "get", "url"],
    5_000,
  ).catch(() => null);
  const url = urlRun?.exitCode === 0 ? urlRun.stdout.trim() : "";
  return {
    contentType: screenshotContentType(buffer),
    imageBase64: buffer.toString("base64"),
    ...(url ? { url } : {}),
  };
}

function updateSession(session: LocalSocialSession): LocalSocialSession {
  const now = nowMs();
  const updated = {
    ...session,
    updatedAt: now,
    expiresAt: now + SESSION_TTL_MS,
  };
  LOCAL_SOCIAL_SESSIONS.set(session.sessionId, updated);
  return updated;
}

function commandAction(body: Record<string, unknown>): SocialCommandAction | null {
  const action = body.action;
  return action === "screenshot" ||
    action === "navigate" ||
    action === "click" ||
    action === "type" ||
    action === "key"
    ? action
    : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

async function runSocialCommand(
  ctx: HttpServerCtx,
  session: LocalSocialSession,
  body: Record<string, unknown>,
): Promise<void> {
  const action = commandAction(body);
  if (!action) throw new Error("unsupported social browser command");

  if (action === "screenshot") return;
  if (action === "navigate") {
    const url = typeof body.url === "string" ? body.url.trim() : "";
    if (!/^https?:\/\//i.test(url)) throw new Error("valid http(s) url required");
    await runChecked(ctx, ["--session", session.agentSessionName, "open", url]);
    return;
  }
  if (action === "click") {
    const x = finiteNumber(body.x);
    const y = finiteNumber(body.y);
    if (x === null || y === null) throw new Error("finite x/y coordinates required");
    const sx = String(Math.round(x));
    const sy = String(Math.round(y));
    await runChecked(ctx, ["--session", session.agentSessionName, "mouse", "move", sx, sy]);
    await runChecked(ctx, ["--session", session.agentSessionName, "mouse", "down", "left"]);
    await runChecked(ctx, ["--session", session.agentSessionName, "mouse", "up", "left"]);
    return;
  }
  if (action === "type") {
    const text = typeof body.text === "string" ? body.text.slice(0, 4096) : "";
    await runChecked(ctx, ["--session", session.agentSessionName, "keyboard", "type", text]);
    return;
  }
  const key = typeof body.key === "string" ? body.key.trim().slice(0, 80) : "";
  if (!key) throw new Error("key required");
  await runChecked(ctx, ["--session", session.agentSessionName, "press", key]);
}

async function handleSessionList(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  expireSessions();
  writeJson(res, 200, {
    ok: true,
    sessions: [...LOCAL_SOCIAL_SESSIONS.values()].map(publicSession),
  });
}

async function handleSessionCreate(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const body = await readJsonBody(req).catch(() => ({}));
  const provider = providerFrom(isRecord(body) ? body.provider : undefined);
  if (!provider) {
    writeJson(res, 400, { error: "supported_provider_required" });
    return;
  }

  expireSessions();
  const sessionId = randomBytes(16).toString("hex");
  const now = nowMs();
  const session: LocalSocialSession = {
    provider,
    sessionId,
    agentSessionName: sessionName(provider, sessionId),
    createdAt: now,
    updatedAt: now,
    expiresAt: now + SESSION_TTL_MS,
  };

  try {
    await runChecked(ctx, [
      "--session",
      session.agentSessionName,
      "open",
      providerStartUrl(provider),
    ]);
    const updated = updateSession(session);
    const screenshot = await captureScreenshot(ctx, updated);
    writeJson(res, 201, {
      ok: true,
      session: publicSession(updated),
      screenshot,
    });
  } catch (err) {
    LOCAL_SOCIAL_SESSIONS.delete(sessionId);
    writeJson(res, 502, {
      error: "social_browser_unavailable",
      message: err instanceof Error ? err.message : String(err),
    });
  }
}

async function handleSessionCommand(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  expireSessions();
  const sessionId = decodePathPart(match[1] ?? "");
  const session = sessionId ? LOCAL_SOCIAL_SESSIONS.get(sessionId) : undefined;
  if (!session) {
    writeJson(res, 404, { error: "session_not_found" });
    return;
  }
  const body = await readJsonBody(req).catch(() => ({}));
  if (!isRecord(body)) {
    writeJson(res, 400, { error: "invalid_social_browser_command" });
    return;
  }

  try {
    await runSocialCommand(ctx, session, body);
    const updated = updateSession(session);
    const screenshot = await captureScreenshot(ctx, updated);
    writeJson(res, 200, {
      ok: true,
      ...screenshot,
      session: publicSession(updated),
    });
  } catch (err) {
    writeJson(res, 400, {
      error: "social_browser_command_failed",
      message: err instanceof Error ? err.message : String(err),
    });
  }
}

async function handleSessionDelete(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const sessionId = decodePathPart(match[1] ?? "");
  const session = sessionId ? LOCAL_SOCIAL_SESSIONS.get(sessionId) : undefined;
  if (!session) {
    writeJson(res, 404, { error: "session_not_found" });
    return;
  }
  await runAgentBrowser(ctx, ["--session", session.agentSessionName, "close"], 5_000).catch(
    () => null,
  );
  LOCAL_SOCIAL_SESSIONS.delete(session.sessionId);
  writeJson(res, 200, { ok: true, status: "closed", sessionId: session.sessionId });
}

export const appSocialBrowserRoutes: RouteHandler[] = [
  route("GET", /^\/v1\/app\/social-browser\/session(?:\?.*)?$/, handleSessionList),
  route("POST", /^\/v1\/app\/social-browser\/session(?:\?.*)?$/, handleSessionCreate),
  route(
    "POST",
    /^\/v1\/app\/social-browser\/session\/([^/?]+)\/command(?:\?.*)?$/,
    handleSessionCommand,
  ),
  route(
    "DELETE",
    /^\/v1\/app\/social-browser\/session\/([^/?]+)(?:\?.*)?$/,
    handleSessionDelete,
  ),
];
