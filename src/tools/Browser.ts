import { spawn } from "node:child_process";
import { lookup } from "node:dns/promises";
import fs from "node:fs/promises";
import { isIP } from "node:net";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { withMagiBinPath } from "../util/shellPath.js";

type BrowserAction =
  | "create_session"
  | "open"
  | "snapshot"
  | "scrape"
  | "click"
  | "fill"
  | "scroll"
  | "screenshot"
  | "close_session";

export interface BrowserInput {
  action: BrowserAction;
  url?: string;
  selector?: string;
  text?: string;
  direction?: "up" | "down" | "left" | "right";
  path?: string;
  replaceExisting?: boolean;
  timeoutMs?: number;
}

export interface BrowserOutput {
  action: BrowserAction;
  sessionId?: string;
  cdpEndpoint?: string;
  stdout?: string;
  stderr?: string;
  exitCode?: number | null;
  signal?: string | null;
  truncated?: boolean;
}

export interface BrowserRunResult {
  exitCode: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
  truncated: boolean;
}

export type BrowserRunner = (
  command: string,
  args: string[],
  ctx: ToolContext,
  timeoutMs: number,
  cwd: string,
) => Promise<BrowserRunResult>;

export interface BrowserHostAddress {
  address: string;
  family: 4 | 6;
}

export type BrowserHostResolver = (hostname: string) => Promise<BrowserHostAddress[]>;

interface BrowserSession {
  sessionId: string;
  cdpEndpoint: string;
  createdAt: number;
  lastUsedAt: number;
}

interface BrowserToolOptions {
  runner?: BrowserRunner;
  resolveHost?: BrowserHostResolver;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: {
      type: "string",
      enum: [
        "create_session",
        "open",
        "snapshot",
        "scrape",
        "click",
        "fill",
        "scroll",
        "screenshot",
        "close_session",
      ],
      description: "Browser operation to perform.",
    },
    url: { type: "string", description: "Public HTTP(S) URL for action=open." },
    selector: { type: "string", description: "Element selector or @ref for click/fill." },
    text: { type: "string", description: "Text for fill." },
    direction: {
      type: "string",
      enum: ["up", "down", "left", "right"],
      description: "Scroll direction.",
    },
    path: { type: "string", description: "Workspace-relative screenshot path." },
    replaceExisting: {
      type: "boolean",
      description: "When true, create_session replaces an existing active browser session.",
    },
    timeoutMs: {
      type: "integer",
      minimum: 100,
      maximum: 120000,
      description: "Command timeout in ms. Defaults to 30000, or 60000 for open/scrape/screenshot.",
    },
  },
  required: ["action"],
  additionalProperties: false,
} as const;

const DEFAULT_TIMEOUT_MS = 30_000;
const SLOW_ACTION_TIMEOUT_MS = 60_000;
const MAX_TIMEOUT_MS = 120_000;
const MAX_OUTPUT_BYTES = 128 * 1024;
const BLOCKED_EXACT_HOSTS = new Set([
  "localhost",
  "metadata",
  "metadata.google.internal",
  "metadata.azure.com",
  "kubernetes.default",
  "kubernetes.default.svc",
  "kubernetes.default.svc.cluster.local",
]);

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function defaultTimeoutForAction(action: BrowserAction): number {
  return action === "open" || action === "scrape" || action === "screenshot"
    ? SLOW_ACTION_TIMEOUT_MS
    : DEFAULT_TIMEOUT_MS;
}

function normalizeTimeout(timeoutMs: unknown, defaultTimeoutMs: number): number {
  if (typeof timeoutMs !== "number" || !Number.isFinite(timeoutMs)) return defaultTimeoutMs;
  return Math.max(100, Math.min(MAX_TIMEOUT_MS, Math.trunc(timeoutMs)));
}

async function defaultResolveHost(hostname: string): Promise<BrowserHostAddress[]> {
  const records = await lookup(hostname, { all: true, verbatim: false });
  return records.flatMap((record) => {
    if (record.family !== 4 && record.family !== 6) return [];
    return [{ address: record.address, family: record.family }];
  });
}

function normalizeHostname(hostname: string): string {
  return hostname.trim().toLowerCase().replace(/^\[/, "").replace(/\]$/, "").replace(/\.$/, "");
}

function isInternalHostname(hostname: string): boolean {
  const host = normalizeHostname(hostname);
  if (BLOCKED_EXACT_HOSTS.has(host)) return true;
  if (!host.includes(".") && isIP(host) === 0) return true;
  return (
    host.endsWith(".localhost") ||
    host.endsWith(".local") ||
    host.endsWith(".internal") ||
    host.endsWith(".svc") ||
    host.endsWith(".svc.cluster.local") ||
    host.endsWith(".cluster.local")
  );
}

function parseIpv4(address: string): number[] | null {
  const parts = address.split(".");
  if (parts.length !== 4) return null;
  const octets = parts.map((part) => {
    if (!/^\d{1,3}$/.test(part)) return Number.NaN;
    const value = Number.parseInt(part, 10);
    return value >= 0 && value <= 255 ? value : Number.NaN;
  });
  return octets.every((octet) => Number.isInteger(octet)) ? octets : null;
}

function isBlockedIpv4(address: string): boolean {
  const octets = parseIpv4(address);
  if (!octets) return true;
  const a = octets[0] ?? -1;
  const b = octets[1] ?? -1;
  const c = octets[2] ?? -1;
  const d = octets[3] ?? -1;

  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 192 && b === 0 && c === 0) ||
    (a === 192 && b === 0 && c === 2) ||
    (a === 198 && (b === 18 || b === 19)) ||
    (a === 198 && b === 51 && c === 100) ||
    (a === 203 && b === 0 && c === 113) ||
    a >= 224 ||
    (a === 255 && b === 255 && c === 255 && d === 255)
  );
}

function isBlockedIpv6(address: string): boolean {
  const lower = address.toLowerCase();
  if (lower === "::" || lower === "::1") return true;
  if (lower.startsWith("::ffff:")) {
    const mapped = lower.slice("::ffff:".length);
    if (isIP(mapped) === 4) return isBlockedIpv4(mapped);
  }
  const first = Number.parseInt(lower.split(":")[0] || "0", 16);
  if (!Number.isFinite(first)) return true;
  return (
    (first & 0xfe00) === 0xfc00 ||
    (first & 0xffc0) === 0xfe80 ||
    (first & 0xff00) === 0xff00 ||
    lower.startsWith("2001:db8:")
  );
}

function isBlockedIpAddress(address: string): boolean {
  const normalized = normalizeHostname(address);
  const family = isIP(normalized);
  if (family === 4) return isBlockedIpv4(normalized);
  if (family === 6) return isBlockedIpv6(normalized);
  return true;
}

async function validateBrowserUrl(raw: string, resolveHost: BrowserHostResolver): Promise<string | null> {
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return `unsupported browser URL scheme: ${parsed.protocol}`;
    }
    const hostname = normalizeHostname(parsed.hostname);
    if (!hostname) return "browser URL host is required";
    if (isInternalHostname(hostname)) {
      return "browser URL host must be a public internet host";
    }
    const directIpFamily = isIP(hostname);
    if (directIpFamily !== 0) {
      return isBlockedIpAddress(hostname) ? "browser URL IP must be public" : null;
    }

    let addresses: BrowserHostAddress[];
    try {
      addresses = await resolveHost(hostname);
    } catch {
      return "browser URL host could not be resolved";
    }
    if (addresses.length === 0) return "browser URL host did not resolve";
    if (addresses.some((address) => isBlockedIpAddress(address.address))) {
      return "browser URL DNS result must be public";
    }
    return null;
  } catch {
    return "invalid browser URL";
  }
}

function resolveWorkspacePath(workspaceRoot: string, relPath: string): string | null {
  if (!stringValue(relPath)) return null;
  const root = path.resolve(workspaceRoot);
  const resolved = path.resolve(root, relPath);
  if (resolved === root || resolved.startsWith(`${root}${path.sep}`)) return resolved;
  return null;
}

function okResult(output: BrowserOutput, start: number): ToolResult<BrowserOutput> {
  return {
    status: "ok",
    output,
    durationMs: Date.now() - start,
  };
}

function errorResult(
  code: string,
  message: string,
  start: number,
  output?: Partial<BrowserOutput>,
): ToolResult<BrowserOutput> {
  return {
    status: "error",
    output: output as BrowserOutput | undefined,
    errorCode: code,
    errorMessage: message,
    durationMs: Date.now() - start,
  };
}

async function defaultRunner(
  command: string,
  args: string[],
  ctx: ToolContext,
  timeoutMs: number,
  cwd: string,
): Promise<BrowserRunResult> {
  return new Promise<BrowserRunResult>((resolve) => {
    const child = spawn(command, args, {
      cwd,
      env: { ...withMagiBinPath(process.env), PWD: cwd },
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let truncated = false;
    const capture = (chunk: Buffer, which: "stdout" | "stderr"): void => {
      const current = which === "stdout" ? stdout : stderr;
      if (current.length >= MAX_OUTPUT_BYTES) {
        truncated = true;
        return;
      }
      const room = MAX_OUTPUT_BYTES - current.length;
      const piece = chunk.toString("utf8");
      if (which === "stdout") stdout += piece.slice(0, room);
      else stderr += piece.slice(0, room);
      if (piece.length > room) truncated = true;
    };
    child.stdout.on("data", (chunk: Buffer) => capture(chunk, "stdout"));
    child.stderr.on("data", (chunk: Buffer) => capture(chunk, "stderr"));

    const timeout = setTimeout(() => {
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 3_000).unref();
    }, timeoutMs);

    ctx.abortSignal.addEventListener("abort", () => child.kill("SIGTERM"), { once: true });

    child.on("close", (exitCode, signal) => {
      clearTimeout(timeout);
      resolve({ exitCode, signal, stdout, stderr, truncated });
    });
    child.on("error", (err) => {
      clearTimeout(timeout);
      resolve({
        exitCode: 127,
        signal: null,
        stdout,
        stderr: err instanceof Error ? err.message : String(err),
        truncated,
      });
    });
  });
}

function parseSession(stdout: string): { sessionId: string; cdpEndpoint: string } | null {
  try {
    const parsed = JSON.parse(stdout);
    const sessionId = stringValue(parsed?.sessionId);
    const cdpEndpoint = stringValue(parsed?.cdpEndpoint);
    if (!sessionId || !cdpEndpoint) return null;
    return { sessionId, cdpEndpoint };
  } catch {
    return null;
  }
}

function commandOutput(
  action: BrowserAction,
  session: BrowserSession,
  run: BrowserRunResult,
): BrowserOutput {
  return {
    action,
    sessionId: session.sessionId,
    cdpEndpoint: session.cdpEndpoint,
    stdout: run.stdout,
    stderr: run.stderr,
    exitCode: run.exitCode,
    signal: run.signal,
    truncated: run.truncated,
  };
}

async function closeBrowserSession(
  session: BrowserSession,
  runner: BrowserRunner,
  ctx: ToolContext,
  timeoutMs: number,
  cwd: string,
): Promise<BrowserRunResult> {
  return runner(
    "integration.sh",
    [`browser/session-close?sessionId=${encodeURIComponent(session.sessionId)}`],
    ctx,
    timeoutMs,
    cwd,
  );
}

export function validateBrowserInput(input: BrowserInput): string | null {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return "`input` must be an object";
  }
  if (![
    "create_session",
    "open",
    "snapshot",
    "scrape",
    "click",
    "fill",
    "scroll",
    "screenshot",
    "close_session",
  ].includes(input.action)) {
    return "`action` must be a supported browser operation";
  }
  if (input.action === "open" && !stringValue(input.url)) return "`url` is required for open";
  if (input.action === "click" && !stringValue(input.selector)) return "`selector` is required for click";
  if (input.action === "fill" && (!stringValue(input.selector) || input.text === undefined)) {
    return "`selector` and `text` are required for fill";
  }
  if (input.action === "scroll" && !["up", "down", "left", "right"].includes(input.direction ?? "")) {
    return "`direction` must be up, down, left, or right for scroll";
  }
  if (input.action === "screenshot" && !stringValue(input.path)) {
    return "`path` is required for screenshot";
  }
  return null;
}

export function makeBrowserTool(
  workspaceRoot: string,
  opts: BrowserToolOptions = {},
): Tool<BrowserInput, BrowserOutput> {
  const runner = opts.runner ?? defaultRunner;
  const resolveHost = opts.resolveHost ?? defaultResolveHost;
  const sessions = new Map<string, BrowserSession>();
  return {
    name: "Browser",
    description:
      "Use the centralized browser-worker through agent-browser for interactive or JS-rendered public websites. Create a session before open/snapshot/scrape/click/fill/scroll/screenshot, then close it when done.",
    inputSchema: INPUT_SCHEMA,
    permission: "net",
    dangerous: false,
    validate: validateBrowserInput,
    async execute(input: BrowserInput, ctx: ToolContext): Promise<ToolResult<BrowserOutput>> {
      const start = Date.now();
      const cwd = ctx.spawnWorkspace?.root ?? workspaceRoot;
      const validationError = validateBrowserInput(input);
      if (validationError) {
        return errorResult("invalid_input", validationError, start);
      }
      const timeoutMs = normalizeTimeout(input.timeoutMs, defaultTimeoutForAction(input.action));

      if (input.action === "create_session") {
        const existing = sessions.get(ctx.sessionKey);
        if (existing && !input.replaceExisting) {
          existing.lastUsedAt = Date.now();
          return okResult({
            action: "create_session",
            sessionId: existing.sessionId,
            cdpEndpoint: existing.cdpEndpoint,
          }, start);
        }
        if (existing) {
          const closeRun = await closeBrowserSession(existing, runner, ctx, timeoutMs, cwd);
          if (closeRun.exitCode !== 0) {
            const output = commandOutput("close_session", existing, closeRun);
            return errorResult(
              "session_close_failed",
              closeRun.stderr || closeRun.stdout || "existing browser session close failed",
              start,
              output,
            );
          }
          sessions.delete(ctx.sessionKey);
        }
        const run = await runner("integration.sh", ["browser/session-create"], ctx, timeoutMs, cwd);
        if (run.exitCode !== 0) {
          return errorResult("session_create_failed", run.stderr || run.stdout || "browser session create failed", start, {
            action: "create_session",
            stdout: run.stdout,
            stderr: run.stderr,
            exitCode: run.exitCode,
            signal: run.signal,
            truncated: run.truncated,
          });
        }
        const parsed = parseSession(run.stdout);
        if (!parsed) {
          return errorResult("session_create_failed", "browser session response missing sessionId or cdpEndpoint", start, {
            action: "create_session",
            stdout: run.stdout,
            stderr: run.stderr,
            exitCode: run.exitCode,
            signal: run.signal,
            truncated: run.truncated,
          });
        }
        const session: BrowserSession = {
          ...parsed,
          createdAt: Date.now(),
          lastUsedAt: Date.now(),
        };
        sessions.set(ctx.sessionKey, session);
        return okResult({
          action: "create_session",
          sessionId: session.sessionId,
          cdpEndpoint: session.cdpEndpoint,
          stdout: run.stdout,
          stderr: run.stderr,
          exitCode: run.exitCode,
          signal: run.signal,
          truncated: run.truncated,
        }, start);
      }

      if (input.action === "close_session") {
        const existing = sessions.get(ctx.sessionKey);
        if (!existing) {
          return okResult({ action: "close_session" }, start);
        }
        const run = await closeBrowserSession(existing, runner, ctx, timeoutMs, cwd);
        const output = commandOutput("close_session", existing, run);
        if (run.exitCode !== 0) {
          return errorResult("command_failed", run.stderr || run.stdout || "browser session close failed", start, output);
        }
        sessions.delete(ctx.sessionKey);
        return okResult(output, start);
      }

      const session = sessions.get(ctx.sessionKey);
      if (!session) {
        return errorResult("no_active_session", "create a browser session before running browser commands", start, {
          action: input.action,
        });
      }

      if (input.action === "open") {
        const url = stringValue(input.url) ?? "";
        const urlError = await validateBrowserUrl(url, resolveHost);
        if (urlError) {
          return errorResult("invalid_url", urlError, start, { action: "open" });
        }
        const run = await runner(
          "agent-browser",
          ["--cdp", session.cdpEndpoint, "open", url],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput("open", session, run);
        return run.exitCode === 0
          ? okResult(output, start)
          : errorResult("command_failed", run.stderr || run.stdout || "browser open failed", start, output);
      }

      if (input.action === "snapshot" || input.action === "scrape") {
        const run = await runner(
          "agent-browser",
          ["--cdp", session.cdpEndpoint, input.action],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput(input.action, session, run);
        return run.exitCode === 0
          ? okResult(output, start)
          : errorResult("command_failed", run.stderr || run.stdout || `browser ${input.action} failed`, start, output);
      }

      if (input.action === "click") {
        const selector = stringValue(input.selector) ?? "";
        const run = await runner(
          "agent-browser",
          ["--cdp", session.cdpEndpoint, "click", selector],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput("click", session, run);
        return run.exitCode === 0
          ? okResult(output, start)
          : errorResult("command_failed", run.stderr || run.stdout || "browser click failed", start, output);
      }

      if (input.action === "fill") {
        const selector = stringValue(input.selector) ?? "";
        const text = input.text ?? "";
        const run = await runner(
          "agent-browser",
          ["--cdp", session.cdpEndpoint, "fill", selector, text],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput("fill", session, run);
        return run.exitCode === 0
          ? okResult(output, start)
          : errorResult("command_failed", run.stderr || run.stdout || "browser fill failed", start, output);
      }

      if (input.action === "scroll") {
        const direction = input.direction ?? "down";
        const run = await runner(
          "agent-browser",
          ["--cdp", session.cdpEndpoint, "scroll", direction],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput("scroll", session, run);
        return run.exitCode === 0
          ? okResult(output, start)
          : errorResult("command_failed", run.stderr || run.stdout || "browser scroll failed", start, output);
      }

      if (input.action === "screenshot") {
        const screenshotPath = resolveWorkspacePath(cwd, stringValue(input.path) ?? "");
        if (!screenshotPath) {
          return errorResult("invalid_path", "screenshot path must stay inside the workspace", start, {
            action: "screenshot",
          });
        }
        await fs.mkdir(path.dirname(screenshotPath), { recursive: true });
        const run = await runner(
          "agent-browser",
          ["--cdp", session.cdpEndpoint, "screenshot", screenshotPath],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput("screenshot", session, run);
        return run.exitCode === 0
          ? okResult(output, start)
          : errorResult("command_failed", run.stderr || run.stdout || "browser screenshot failed", start, output);
      }

      return errorResult("unsupported_action", `browser action not implemented: ${input.action}`, start, {
        action: input.action,
      });
    },
  };
}
