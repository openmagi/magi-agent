import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { lookup } from "node:dns/promises";
import fs from "node:fs/promises";
import { isIP } from "node:net";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { withMagiBinPath } from "../util/shellPath.js";
import { Utf8StreamCapture } from "../util/Utf8StreamCapture.js";

type BrowserAction =
  | "create_session"
  | "open"
  | "snapshot"
  | "scrape"
  | "click"
  | "fill"
  | "scroll"
  | "screenshot"
  | "mouse_click"
  | "keyboard_type"
  | "press"
  | "close_session";

export interface BrowserInput {
  action: BrowserAction;
  url?: string;
  selector?: string;
  text?: string;
  direction?: "up" | "down" | "left" | "right";
  path?: string;
  x?: number;
  y?: number;
  button?: "left" | "right" | "middle";
  key?: string;
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
  agentBrowserSessionName: string;
  createdAt: number;
  lastUsedAt: number;
  lastUrl?: string;
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
        "mouse_click",
        "keyboard_type",
        "press",
        "close_session",
      ],
      description: "Browser operation to perform.",
    },
    url: { type: "string", description: "Public HTTP(S) URL for action=open." },
    selector: {
      type: "string",
      description: "Element selector or snapshot ref for click/fill. Prefer @e3; [ref=e3] is also accepted.",
    },
    text: { type: "string", description: "Text for fill." },
    direction: {
      type: "string",
      enum: ["up", "down", "left", "right"],
      description: "Scroll direction.",
    },
    path: { type: "string", description: "Workspace-relative screenshot path." },
    x: { type: "number", description: "Viewport X coordinate for mouse_click." },
    y: { type: "number", description: "Viewport Y coordinate for mouse_click." },
    button: {
      type: "string",
      enum: ["left", "right", "middle"],
      description: "Mouse button for mouse_click. Defaults to left.",
    },
    key: { type: "string", description: "Key name for press, such as Enter, Tab, or Control+a." },
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
const MAX_BROWSER_FRAME_BYTES = 750 * 1024;
const BROWSER_FRAME_DIR = ".magi/browser-frames";
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

function contentHash(content: string): string {
  return `sha256:${createHash("sha256").update(content).digest("hex")}`;
}

function extractHtmlTitle(html: string): string | undefined {
  const rawTitle = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html)?.[1];
  const title = rawTitle
    ?.replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, "\"")
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
  return title || undefined;
}

function okResult(
  output: BrowserOutput,
  start: number,
  metadata?: Record<string, unknown>,
): ToolResult<BrowserOutput> {
  return {
    status: "ok",
    output,
    durationMs: Date.now() - start,
    ...(metadata ? { metadata } : {}),
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
    const env: NodeJS.ProcessEnv = { ...withMagiBinPath(process.env), PWD: cwd };
    delete env.BROWSER_CDP_URL;
    const child = spawn(command, args, {
      cwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    const stdout = new Utf8StreamCapture(MAX_OUTPUT_BYTES);
    const stderr = new Utf8StreamCapture(MAX_OUTPUT_BYTES);
    child.stdout.on("data", (chunk: Buffer) => stdout.write(chunk));
    child.stderr.on("data", (chunk: Buffer) => stderr.write(chunk));

    const timeout = setTimeout(() => {
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 3_000).unref();
    }, timeoutMs);

    ctx.abortSignal.addEventListener("abort", () => child.kill("SIGTERM"), { once: true });

    child.on("close", (exitCode, signal) => {
      clearTimeout(timeout);
      resolve({
        exitCode,
        signal,
        stdout: stdout.end(),
        stderr: stderr.end(),
        truncated: stdout.truncated || stderr.truncated,
      });
    });
    child.on("error", (err) => {
      clearTimeout(timeout);
      resolve({
        exitCode: 127,
        signal: null,
        stdout: stdout.end(),
        stderr: err instanceof Error ? err.message : String(err),
        truncated: stdout.truncated || stderr.truncated,
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

function browserFrameContentType(buffer: Buffer): "image/png" | "image/jpeg" {
  if (
    buffer.length >= 3 &&
    buffer[0] === 0xff &&
    buffer[1] === 0xd8 &&
    buffer[2] === 0xff
  ) {
    return "image/jpeg";
  }
  return "image/png";
}

function safeFrameName(turnId: string, action: BrowserAction): string {
  const safeTurn = turnId.replace(/[^a-zA-Z0-9_.-]/g, "-") || "turn";
  const safeAction = action.replace(/[^a-zA-Z0-9_.-]/g, "-") || "browser";
  return `${safeTurn}-${Date.now()}-${safeAction}.png`;
}

async function emitBrowserFrameFromFile(input: {
  action: BrowserAction;
  url?: string;
  filePath: string;
  ctx: ToolContext;
  removeAfterRead?: boolean;
}): Promise<void> {
  if (!input.ctx.emitAgentEvent) return;
  try {
    const stat = await fs.stat(input.filePath);
    if (!stat.isFile() || stat.size <= 0 || stat.size > MAX_BROWSER_FRAME_BYTES) {
      if (input.removeAfterRead) await fs.rm(input.filePath, { force: true });
      return;
    }
    const buffer = await fs.readFile(input.filePath);
    input.ctx.emitAgentEvent({
      type: "browser_frame",
      action: input.action,
      ...(input.url ? { url: input.url } : {}),
      imageBase64: buffer.toString("base64"),
      contentType: browserFrameContentType(buffer),
      capturedAt: Date.now(),
    });
    if (input.removeAfterRead) {
      await fs.rm(input.filePath, { force: true });
    }
  } catch {
    // Browser previews are best-effort UI telemetry; never fail the tool.
  }
}

async function emitBrowserFrame(input: {
  action: BrowserAction;
  session: BrowserSession;
  ctx: ToolContext;
  runner: BrowserRunner;
  cwd: string;
  timeoutMs: number;
  url?: string;
  existingScreenshotPath?: string;
}): Promise<void> {
  if (!input.ctx.emitAgentEvent) return;
  if (input.existingScreenshotPath) {
    await emitBrowserFrameFromFile({
      action: input.action,
      url: input.url,
      filePath: input.existingScreenshotPath,
      ctx: input.ctx,
    });
    return;
  }

  const frameDir = path.resolve(input.cwd, BROWSER_FRAME_DIR);
  const framePath = path.join(frameDir, safeFrameName(input.ctx.turnId, input.action));
  try {
    await fs.mkdir(frameDir, { recursive: true });
    const run = await input.runner(
      "agent-browser",
      ["--session", input.session.agentBrowserSessionName, "screenshot", framePath],
      input.ctx,
      Math.min(input.timeoutMs, DEFAULT_TIMEOUT_MS),
      input.cwd,
    );
    if (run.exitCode !== 0) return;
    await emitBrowserFrameFromFile({
      action: input.action,
      url: input.url,
      filePath: framePath,
      ctx: input.ctx,
      removeAfterRead: true,
    });
  } catch {
    // Browser previews are best-effort UI telemetry; never fail the tool.
  }
}

function agentBrowserSessionName(sessionId: string): string {
  return `magi-browser-${sessionId.replace(/[^a-zA-Z0-9_.-]/g, "-")}`;
}

interface SnapshotSelector {
  ref: string | null;
  role: string | null;
  label: string | null;
}

function parseSnapshotSelector(selector: string): SnapshotSelector {
  const trimmed = selector.trim();
  const refMatch = trimmed.match(/\[ref=([a-zA-Z0-9_.:-]+)\]$/);
  const ref = refMatch?.[1] ?? null;
  const withoutRef = refMatch ? trimmed.slice(0, refMatch.index).trim() : trimmed;
  const roleLabelMatch = withoutRef.match(/^([a-zA-Z][\w-]*)(?:\s+"([^"]*)")?$/);
  return {
    ref,
    role: roleLabelMatch?.[1] ?? null,
    label: roleLabelMatch?.[2] ?? null,
  };
}

function normalizeAgentBrowserSelector(selector: string): string {
  const trimmed = selector.trim();
  if (trimmed.startsWith("@")) return trimmed;

  const parsed = parseSnapshotSelector(trimmed);
  if (!parsed.ref) return trimmed;

  const prefix = trimmed.slice(0, trimmed.lastIndexOf("[ref=")).trim();
  const isSnapshotRoleLabel =
    prefix.length === 0 || /^[a-zA-Z][\w-]*(?:\s+"[^"]*")?$/.test(prefix);
  return isSnapshotRoleLabel ? `@${parsed.ref}` : trimmed;
}

function pushUniqueArgSet(target: string[][], args: string[]): void {
  if (
    !target.some((existing) =>
      existing.length === args.length &&
      existing.every((value, index) => value === args[index]),
    )
  ) {
    target.push(args);
  }
}

function buildSelectorFallbackArgs(
  action: "click" | "fill",
  selector: string,
  text = "",
): string[][] {
  const parsed = parseSnapshotSelector(selector);
  const label = parsed.label?.trim();
  const role = parsed.role?.trim();
  const fallbacks: string[][] = [];

  if (action === "click") {
    if (role) {
      if (label) pushUniqueArgSet(fallbacks, ["find", "role", role, "click", label]);
      else pushUniqueArgSet(fallbacks, ["find", "role", role, "click"]);
    }
    if (label) pushUniqueArgSet(fallbacks, ["find", "text", label, "click"]);
    return fallbacks;
  }

  if (label) {
    pushUniqueArgSet(fallbacks, ["find", "label", label, "fill", text]);
    pushUniqueArgSet(fallbacks, ["find", "placeholder", label, "fill", text]);
  }
  if (role) pushUniqueArgSet(fallbacks, ["find", "role", role, "fill", text]);
  return fallbacks;
}

async function runSelectorFallbacks(
  action: "click" | "fill",
  session: BrowserSession,
  selector: string,
  text: string,
  runner: BrowserRunner,
  ctx: ToolContext,
  timeoutMs: number,
  cwd: string,
): Promise<BrowserRunResult | null> {
  for (const fallbackArgs of buildSelectorFallbackArgs(action, selector, text)) {
    const run = await runner(
      "agent-browser",
      ["--session", session.agentBrowserSessionName, ...fallbackArgs],
      ctx,
      timeoutMs,
      cwd,
    );
    if (run.exitCode === 0) return run;
  }
  return null;
}

async function waitAndRetrySelectorAction(
  action: "click" | "fill",
  session: BrowserSession,
  selector: string,
  text: string,
  runner: BrowserRunner,
  ctx: ToolContext,
  timeoutMs: number,
  cwd: string,
): Promise<BrowserRunResult | null> {
  const waitRun = await runner(
    "agent-browser",
    ["--session", session.agentBrowserSessionName, "wait", selector],
    ctx,
    timeoutMs,
    cwd,
  );
  if (waitRun.exitCode !== 0) return waitRun;

  return runner(
    "agent-browser",
    action === "click"
      ? ["--session", session.agentBrowserSessionName, "click", selector]
      : ["--session", session.agentBrowserSessionName, "fill", selector, text],
    ctx,
    timeoutMs,
    cwd,
  );
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
    "mouse_click",
    "keyboard_type",
    "press",
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
  if (input.action === "mouse_click") {
    if (
      typeof input.x !== "number" ||
      !Number.isFinite(input.x) ||
      typeof input.y !== "number" ||
      !Number.isFinite(input.y)
    ) {
      return "`x` and `y` are required finite numbers for mouse_click";
    }
    if (input.button && !["left", "right", "middle"].includes(input.button)) {
      return "`button` must be left, right, or middle for mouse_click";
    }
  }
  if (input.action === "keyboard_type" && input.text === undefined) {
    return "`text` is required for keyboard_type";
  }
  if (input.action === "press" && !stringValue(input.key)) {
    return "`key` is required for press";
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
      "Use the centralized browser-worker through agent-browser for interactive or JS-rendered public websites. Create a session before open/snapshot/scrape/click/fill/scroll/screenshot, then close it when done. Snapshot refs may be passed as @e3 or copied from [ref=e3].",
    inputSchema: INPUT_SCHEMA,
    permission: "net",
    shouldDefer: true,
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
          agentBrowserSessionName: agentBrowserSessionName(parsed.sessionId),
          createdAt: Date.now(),
          lastUsedAt: Date.now(),
        };
        const connectRun = await runner(
          "agent-browser",
          ["--session", session.agentBrowserSessionName, "connect", session.cdpEndpoint],
          ctx,
          DEFAULT_TIMEOUT_MS,
          cwd,
        );
        if (connectRun.exitCode !== 0) {
          await closeBrowserSession(session, runner, ctx, timeoutMs, cwd);
          return errorResult(
            "session_connect_failed",
            connectRun.stderr || connectRun.stdout || "browser CDP connect failed",
            start,
            commandOutput("create_session", session, connectRun),
          );
        }
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
          ["--session", session.agentBrowserSessionName, "open", url],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput("open", session, run);
        if (run.exitCode === 0) {
          session.lastUrl = url;
          await emitBrowserFrame({
            action: "open",
            session,
            ctx,
            runner,
            cwd,
            timeoutMs,
            url,
          });
          return okResult(output, start);
        }
        return errorResult("command_failed", run.stderr || run.stdout || "browser open failed", start, output);
      }

      if (input.action === "snapshot" || input.action === "scrape") {
        const args =
          input.action === "scrape"
            ? ["--session", session.agentBrowserSessionName, "get", "html"]
            : ["--session", session.agentBrowserSessionName, "snapshot"];
        const run = await runner(
          "agent-browser",
          args,
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput(input.action, session, run);
        if (run.exitCode === 0) {
          const source = ctx.sourceLedger?.recordSource({
            turnId: ctx.turnId,
            toolName: "Browser",
            kind: "browser",
            uri: session.lastUrl ?? `browser:${session.sessionId}:${input.action}`,
            title: input.action === "scrape"
              ? extractHtmlTitle(run.stdout) ?? session.lastUrl
              : session.lastUrl,
            contentHash: contentHash(run.stdout),
            contentType: input.action === "scrape" ? "text/html" : "text/plain",
            trustTier: "unknown",
            snippets: run.stdout ? [run.stdout.slice(0, 500)] : [],
            metadata: {
              action: input.action,
              truncated: run.truncated,
            },
          });
          if (source) ctx.emitAgentEvent?.({ type: "source_inspected", source });
          await emitBrowserFrame({
            action: input.action,
            session,
            ctx,
            runner,
            cwd,
            timeoutMs,
          });
          return okResult(output, start, source ? { sourceId: source.sourceId } : undefined);
        }
        return errorResult("command_failed", run.stderr || run.stdout || `browser ${input.action} failed`, start, output);
      }

      if (input.action === "click") {
        const rawSelector = stringValue(input.selector) ?? "";
        const selector = normalizeAgentBrowserSelector(rawSelector);
        const run = await runner(
          "agent-browser",
          ["--session", session.agentBrowserSessionName, "click", selector],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const retryRun = run.exitCode === 0
          ? null
          : await waitAndRetrySelectorAction("click", session, selector, "", runner, ctx, timeoutMs, cwd);
        const fallbackRun = retryRun?.exitCode === 0 || run.exitCode === 0
          ? null
          : await runSelectorFallbacks("click", session, rawSelector, "", runner, ctx, timeoutMs, cwd);
        const finalRun = fallbackRun ?? retryRun ?? run;
        const output = commandOutput("click", session, finalRun);
        if (finalRun.exitCode === 0) {
          await emitBrowserFrame({ action: "click", session, ctx, runner, cwd, timeoutMs });
          return okResult(output, start);
        }
        return errorResult(
          "command_failed",
          finalRun.stderr || finalRun.stdout || "browser click failed",
          start,
          output,
        );
      }

      if (input.action === "fill") {
        const rawSelector = stringValue(input.selector) ?? "";
        const selector = normalizeAgentBrowserSelector(rawSelector);
        const text = input.text ?? "";
        const run = await runner(
          "agent-browser",
          ["--session", session.agentBrowserSessionName, "fill", selector, text],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const retryRun = run.exitCode === 0
          ? null
          : await waitAndRetrySelectorAction("fill", session, selector, text, runner, ctx, timeoutMs, cwd);
        const fallbackRun = retryRun?.exitCode === 0 || run.exitCode === 0
          ? null
          : await runSelectorFallbacks("fill", session, rawSelector, text, runner, ctx, timeoutMs, cwd);
        const finalRun = fallbackRun ?? retryRun ?? run;
        const output = commandOutput("fill", session, finalRun);
        if (finalRun.exitCode === 0) {
          await emitBrowserFrame({ action: "fill", session, ctx, runner, cwd, timeoutMs });
          return okResult(output, start);
        }
        return errorResult(
          "command_failed",
          finalRun.stderr || finalRun.stdout || "browser fill failed",
          start,
          output,
        );
      }

      if (input.action === "scroll") {
        const direction = input.direction ?? "down";
        const run = await runner(
          "agent-browser",
          ["--session", session.agentBrowserSessionName, "scroll", direction],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput("scroll", session, run);
        if (run.exitCode === 0) {
          await emitBrowserFrame({ action: "scroll", session, ctx, runner, cwd, timeoutMs });
          return okResult(output, start);
        }
        return errorResult("command_failed", run.stderr || run.stdout || "browser scroll failed", start, output);
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
          ["--session", session.agentBrowserSessionName, "screenshot", screenshotPath],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput("screenshot", session, run);
        if (run.exitCode === 0) {
          await emitBrowserFrame({
            action: "screenshot",
            session,
            ctx,
            runner,
            cwd,
            timeoutMs,
            existingScreenshotPath: screenshotPath,
          });
          return okResult(output, start);
        }
        return errorResult("command_failed", run.stderr || run.stdout || "browser screenshot failed", start, output);
      }

      if (input.action === "mouse_click") {
        const button = input.button ?? "left";
        const x = String(Math.trunc(input.x ?? 0));
        const y = String(Math.trunc(input.y ?? 0));
        const steps: string[][] = [
          ["mouse", "move", x, y],
          ["mouse", "down", button],
          ["mouse", "up", button],
        ];
        let lastRun: BrowserRunResult | null = null;
        for (const step of steps) {
          lastRun = await runner(
            "agent-browser",
            ["--session", session.agentBrowserSessionName, ...step],
            ctx,
            timeoutMs,
            cwd,
          );
          if (lastRun.exitCode !== 0) break;
        }
        session.lastUsedAt = Date.now();
        const finalRun = lastRun ?? {
          exitCode: 1,
          signal: null,
          stdout: "",
          stderr: "browser mouse click failed",
          truncated: false,
        };
        const output = commandOutput("mouse_click", session, finalRun);
        if (finalRun.exitCode === 0) {
          await emitBrowserFrame({ action: "mouse_click", session, ctx, runner, cwd, timeoutMs });
          return okResult(output, start);
        }
        return errorResult(
          "command_failed",
          finalRun.stderr || finalRun.stdout || "browser mouse click failed",
          start,
          output,
        );
      }

      if (input.action === "keyboard_type") {
        const run = await runner(
          "agent-browser",
          ["--session", session.agentBrowserSessionName, "keyboard", "type", input.text ?? ""],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput("keyboard_type", session, run);
        if (run.exitCode === 0) {
          await emitBrowserFrame({ action: "keyboard_type", session, ctx, runner, cwd, timeoutMs });
          return okResult(output, start);
        }
        return errorResult(
          "command_failed",
          run.stderr || run.stdout || "browser keyboard type failed",
          start,
          output,
        );
      }

      if (input.action === "press") {
        const key = stringValue(input.key) ?? "";
        const run = await runner(
          "agent-browser",
          ["--session", session.agentBrowserSessionName, "press", key],
          ctx,
          timeoutMs,
          cwd,
        );
        session.lastUsedAt = Date.now();
        const output = commandOutput("press", session, run);
        if (run.exitCode === 0) {
          await emitBrowserFrame({ action: "press", session, ctx, runner, cwd, timeoutMs });
          return okResult(output, start);
        }
        return errorResult(
          "command_failed",
          run.stderr || run.stdout || "browser key press failed",
          start,
          output,
        );
      }

      return errorResult("unsupported_action", `browser action not implemented: ${input.action}`, start, {
        action: input.action,
      });
    },
  };
}
