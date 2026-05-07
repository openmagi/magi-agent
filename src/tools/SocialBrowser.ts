import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import {
  DEFAULT_SOCIAL_BROWSER_MAX_ITEMS,
  capVisibleText,
  normalizeSocialProvider,
  parseSocialClaim,
  resolveSocialScreenshotPath,
  validateSocialBrowserInput,
  type SocialBrowserAction,
  type SocialBrowserInput,
  type SocialProvider,
} from "../social/SocialBrowserPolicy.js";
import { withMagiBinPath } from "../util/shellPath.js";
import { Utf8StreamCapture } from "../util/Utf8StreamCapture.js";

export interface SocialBrowserOutput {
  action: SocialBrowserAction;
  provider: SocialProvider;
  sessionId?: string;
  maxItems?: number;
  path?: string;
  stdout?: string;
  stderr?: string;
  exitCode?: number | null;
  signal?: string | null;
  truncated?: boolean;
}

export interface SocialBrowserRunResult {
  exitCode: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
  truncated: boolean;
}

export type SocialBrowserRunner = (
  command: string,
  args: string[],
  ctx: ToolContext,
  timeoutMs: number,
  cwd: string,
) => Promise<SocialBrowserRunResult>;

interface SocialBrowserToolOptions {
  runner?: SocialBrowserRunner;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: {
      type: "string",
      enum: ["status", "open", "snapshot", "scrape_visible", "screenshot", "close"],
      description: "Read-only social browser operation.",
    },
    provider: {
      type: "string",
      enum: ["instagram", "ig", "x", "twitter"],
      description: "Social provider for the existing dashboard-created session.",
    },
    url: {
      type: "string",
      description: "Provider-scoped HTTPS URL for action=open.",
    },
    path: {
      type: "string",
      description: "Workspace-relative screenshot path for action=screenshot.",
    },
    maxItems: {
      type: "integer",
      minimum: 1,
      maximum: DEFAULT_SOCIAL_BROWSER_MAX_ITEMS,
      description: "Maximum visible items to read. Platform cap is 20.",
    },
    timeoutMs: {
      type: "integer",
      minimum: 100,
      maximum: 120000,
      description: "Command timeout in ms.",
    },
  },
  required: ["action", "provider"],
  additionalProperties: false,
} as const;

const DEFAULT_TIMEOUT_MS = 30_000;
const SLOW_ACTION_TIMEOUT_MS = 60_000;
const MAX_TIMEOUT_MS = 120_000;
const MAX_OUTPUT_BYTES = 128 * 1024;

function normalizeTimeout(timeoutMs: unknown, defaultTimeoutMs: number): number {
  if (typeof timeoutMs !== "number" || !Number.isFinite(timeoutMs)) return defaultTimeoutMs;
  return Math.max(100, Math.min(MAX_TIMEOUT_MS, Math.trunc(timeoutMs)));
}

function timeoutForAction(action: SocialBrowserAction): number {
  return action === "open" || action === "scrape_visible" || action === "screenshot"
    ? SLOW_ACTION_TIMEOUT_MS
    : DEFAULT_TIMEOUT_MS;
}

function normalizeMaxItems(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return DEFAULT_SOCIAL_BROWSER_MAX_ITEMS;
  return Math.max(1, Math.min(DEFAULT_SOCIAL_BROWSER_MAX_ITEMS, Math.trunc(value)));
}

function browserCommandEnv(cwd: string): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...withMagiBinPath(process.env), PWD: cwd };
  delete env.BROWSER_CDP_URL;
  return env;
}

function agentBrowserSessionName(provider: SocialProvider, sessionId: string): string {
  const safeProvider = provider.replace(/[^a-zA-Z0-9_.-]/g, "-");
  const safeSessionId = sessionId.replace(/[^a-zA-Z0-9_.-]/g, "-");
  return `magi-social-${safeProvider}-${safeSessionId}`;
}

function okResult(output: SocialBrowserOutput, start: number): ToolResult<SocialBrowserOutput> {
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
  output?: Partial<SocialBrowserOutput>,
): ToolResult<SocialBrowserOutput> {
  return {
    status: "error",
    output: output as SocialBrowserOutput | undefined,
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
): Promise<SocialBrowserRunResult> {
  return new Promise<SocialBrowserRunResult>((resolve) => {
    const child = spawn(command, args, {
      cwd,
      env: browserCommandEnv(cwd),
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

function outputFromRun(
  action: SocialBrowserAction,
  provider: SocialProvider,
  run: SocialBrowserRunResult,
  extras: Partial<SocialBrowserOutput> = {},
): SocialBrowserOutput {
  return {
    action,
    provider,
    stdout: run.stdout,
    stderr: run.stderr,
    exitCode: run.exitCode,
    signal: run.signal,
    truncated: run.truncated,
    ...extras,
  };
}

function redactClaimStdout(stdout: string): string {
  if (!stdout) return stdout;
  try {
    const redact = (value: unknown): unknown => {
      if (Array.isArray(value)) return value.map(redact);
      if (!value || typeof value !== "object") return value;
      const output: Record<string, unknown> = {};
      for (const [key, child] of Object.entries(value)) {
        if (key === "cdpEndpoint" || key === "cdpToken") {
          output[key] = "[redacted]";
        } else {
          output[key] = redact(child);
        }
      }
      return output;
    };
    return JSON.stringify(redact(JSON.parse(stdout)));
  } catch {
    return stdout
      .replace(/("cdpEndpoint"\s*:\s*")[^"]+(")/g, "$1[redacted]$2")
      .replace(/("cdpToken"\s*:\s*")[^"]+(")/g, "$1[redacted]$2")
      .replace(/(token=)[^&\s"]+/gi, "$1[redacted]");
  }
}

export function makeSocialBrowserTool(
  workspaceRoot: string,
  opts: SocialBrowserToolOptions = {},
): Tool<SocialBrowserInput, SocialBrowserOutput> {
  const runner = opts.runner ?? defaultRunner;
  return {
    name: "SocialBrowser",
    description:
      "Read the user's one-time Instagram/X browser session created from dashboard Integrations. Use only for visible-page reads; never ask for, store, or replay social passwords.",
    inputSchema: INPUT_SCHEMA,
    permission: "net",
    dangerous: false,
    validate: validateSocialBrowserInput,
    async execute(input: SocialBrowserInput, ctx: ToolContext): Promise<ToolResult<SocialBrowserOutput>> {
      const start = Date.now();
      const validationError = validateSocialBrowserInput(input);
      const provider = normalizeSocialProvider(input.provider);
      if (validationError || !provider) {
        return errorResult("invalid_input", validationError || "`provider` must be instagram or x", start);
      }

      const cwd = ctx.spawnWorkspace?.root ?? workspaceRoot;
      const timeoutMs = normalizeTimeout(input.timeoutMs, timeoutForAction(input.action));

      if (input.action === "status" || input.action === "close") {
        const run = await runner(
          "integration.sh",
          [`social-browser/${input.action}?provider=${encodeURIComponent(provider)}`],
          ctx,
          timeoutMs,
          cwd,
        );
        const output = outputFromRun(input.action, provider, { ...run, stdout: redactClaimStdout(run.stdout) });
        return run.exitCode === 0
          ? okResult(output, start)
          : errorResult("command_failed", run.stderr || output.stdout || `social browser ${input.action} failed`, start, output);
      }

      let screenshotPath: string | null = null;
      if (input.action === "screenshot") {
        screenshotPath = resolveSocialScreenshotPath(cwd, input.path || "");
        if (!screenshotPath) {
          return errorResult("invalid_path", "screenshot path must stay inside the workspace", start, {
            action: "screenshot",
            provider,
          });
        }
        await fs.mkdir(path.dirname(screenshotPath), { recursive: true });
      }

      const claimMaxItems = normalizeMaxItems(input.maxItems);
      const claimRun = await runner(
        "integration.sh",
        ["social-browser/claim", JSON.stringify({ provider, maxItems: claimMaxItems })],
        ctx,
        DEFAULT_TIMEOUT_MS,
        cwd,
      );
      if (claimRun.exitCode !== 0) {
        const safeStdout = redactClaimStdout(claimRun.stdout);
        const safeStderr = redactClaimStdout(claimRun.stderr);
        return errorResult("session_claim_failed", safeStderr || safeStdout || "social browser session claim failed", start, {
          action: input.action,
          provider,
          stdout: safeStdout,
          stderr: safeStderr,
          exitCode: claimRun.exitCode,
          signal: claimRun.signal,
          truncated: claimRun.truncated,
        });
      }
      const claim = parseSocialClaim(claimRun.stdout);
      if (!claim) {
        const safeStdout = redactClaimStdout(claimRun.stdout);
        const safeStderr = redactClaimStdout(claimRun.stderr);
        return errorResult("session_claim_failed", "social browser claim response missing sessionId or cdpEndpoint", start, {
          action: input.action,
          provider,
          stdout: safeStdout,
          stderr: safeStderr,
          exitCode: claimRun.exitCode,
          signal: claimRun.signal,
          truncated: claimRun.truncated,
        });
      }

      const localSessionName = agentBrowserSessionName(provider, claim.sessionId);
      const connectRun = await runner(
        "agent-browser",
        ["--session", localSessionName, "connect", claim.cdpEndpoint],
        ctx,
        DEFAULT_TIMEOUT_MS,
        cwd,
      );
      if (connectRun.exitCode !== 0) {
        const safeRun = {
          ...connectRun,
          stdout: redactClaimStdout(connectRun.stdout),
          stderr: redactClaimStdout(connectRun.stderr),
        };
        return errorResult(
          "session_connect_failed",
          safeRun.stderr || safeRun.stdout || "social browser CDP connect failed",
          start,
          outputFromRun(input.action, provider, safeRun, {
            sessionId: claim.sessionId,
            maxItems: claim.maxItems,
          }),
        );
      }

      let args: string[];
      if (input.action === "open") {
        args = ["--session", localSessionName, "open", input.url || ""];
      } else if (input.action === "snapshot") {
        args = ["--session", localSessionName, "snapshot"];
      } else if (input.action === "scrape_visible") {
        args = ["--session", localSessionName, "scrape"];
      } else {
        args = ["--session", localSessionName, "screenshot", screenshotPath || ""];
      }

      const run = await runner("agent-browser", args, ctx, timeoutMs, cwd);
      const stdout = input.action === "scrape_visible"
        ? capVisibleText(redactClaimStdout(run.stdout), claim.maxItems)
        : redactClaimStdout(run.stdout);
      const safeRun = { ...run, stdout, stderr: redactClaimStdout(run.stderr) };
      const output = outputFromRun(input.action, provider, safeRun, {
        sessionId: claim.sessionId,
        maxItems: claim.maxItems,
        ...(screenshotPath ? { path: screenshotPath } : {}),
      });
      return run.exitCode === 0
        ? okResult(output, start)
        : errorResult("command_failed", safeRun.stderr || safeRun.stdout || `social browser ${input.action} failed`, start, output);
    },
  };
}
