import type { HookPoint, RegisteredHook } from "../hooks/types.js";
import type { HookRegistry } from "../hooks/HookRegistry.js";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { withClawyBinPath } from "../util/shellPath.js";

const SUPPORTED_POINTS = new Set<HookPoint>([
  "beforeToolUse",
  "afterToolUse",
  "beforeCommit",
]);
const SUPPORTED_DECISIONS = new Set(["approve", "deny", "ask"]);
const SUPPORTED_ACTIONS = new Set(["block", "permission_decision"]);
const CLAWY_SKILL_MANIFEST = ".clawy-skill-manifest.json";

export interface RawSkillRuntimeHook {
  name?: unknown;
  point?: unknown;
  if?: unknown;
  action?: unknown;
  decision?: unknown;
  reason?: unknown;
  priority?: unknown;
  blocking?: unknown;
}

export interface SkillRuntimeHookDeclaration {
  skillName: string;
  name: string;
  point: "beforeToolUse" | "afterToolUse" | "beforeCommit";
  if: string;
  action: "block" | "permission_decision" | "command";
  decision?: "approve" | "deny" | "ask";
  reason: string;
  priority: number;
  blocking: boolean;
  timeoutMs?: number;
  trustSource?: "static" | "trusted_root" | "admin_trusted" | "manifest";
  command?: {
    path: string;
    skillRoot: string;
    timeoutMs: number;
    once: boolean;
    statusMessage?: string;
  };
}

export interface SkillRuntimeHookIssue {
  skillName: string;
  index: number;
  reason: string;
}

export function normalizeSkillRuntimeHooks(
  skillName: string,
  raw: unknown,
): { hooks: SkillRuntimeHookDeclaration[]; issues: SkillRuntimeHookIssue[] } {
  if (raw === undefined || raw === null) return { hooks: [], issues: [] };
  if (!Array.isArray(raw)) {
    return {
      hooks: [],
      issues: [{ skillName, index: -1, reason: "`runtime_hooks` must be an array" }],
    };
  }

  const hooks: SkillRuntimeHookDeclaration[] = [];
  const issues: SkillRuntimeHookIssue[] = [];

  for (let i = 0; i < raw.length; i++) {
    const item = raw[i] as RawSkillRuntimeHook;
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      issues.push({ skillName, index: i, reason: "hook must be an object" });
      continue;
    }

    const point = typeof item.point === "string" ? item.point : "";
    if (!SUPPORTED_POINTS.has(point as HookPoint)) {
      issues.push({ skillName, index: i, reason: "unsupported hook point" });
      continue;
    }

    const rule = typeof item.if === "string" ? item.if.trim() : "";
    if (!rule) {
      issues.push({ skillName, index: i, reason: "`if` rule is required" });
      continue;
    }

    const actionRaw = typeof item.action === "string" ? item.action.trim() : "";
    const decisionRaw = typeof item.decision === "string" ? item.decision.trim() : "";
    const action =
      actionRaw && SUPPORTED_ACTIONS.has(actionRaw)
        ? (actionRaw as "block" | "permission_decision")
        : decisionRaw
          ? "permission_decision"
          : "block";

    const decision =
      decisionRaw && SUPPORTED_DECISIONS.has(decisionRaw)
        ? (decisionRaw as "approve" | "deny" | "ask")
        : undefined;

    if (action === "permission_decision" && !decision) {
      issues.push({
        skillName,
        index: i,
        reason: "`permission_decision` hooks require decision=approve|deny|ask",
      });
      continue;
    }

    const name =
      typeof item.name === "string" && item.name.trim()
        ? item.name.trim().replace(/[^a-zA-Z0-9:_-]/g, "-").slice(0, 80)
        : `${point}-${i + 1}`;
    const reason =
      typeof item.reason === "string" && item.reason.trim()
        ? item.reason.trim()
        : `skill ${skillName} runtime hook`;
    const priority =
      typeof item.priority === "number" && Number.isFinite(item.priority)
        ? item.priority
        : 60;
    const blocking = typeof item.blocking === "boolean" ? item.blocking : true;

    hooks.push({
      skillName,
      name,
      point: point as "beforeToolUse" | "afterToolUse" | "beforeCommit",
      if: rule,
      action,
      ...(decision ? { decision } : {}),
      reason,
      priority,
      blocking,
      trustSource: "static",
    });
  }

  return { hooks, issues };
}

export interface ClaudeSkillHookTrustOptions {
  skillName: string;
  skillRoot: string;
  workspaceRoot: string;
  raw: unknown;
  trustedSkillRoots?: readonly string[];
  trustedSkillDirs?: readonly string[];
}

type ClaudeHookPointName =
  | "PreToolUse"
  | "PostToolUse"
  | "PermissionRequest"
  | "PermissionDenied"
  | "Stop";

interface RawClaudeCommandHook {
  matcher?: unknown;
  command?: unknown;
  timeout?: unknown;
  once?: unknown;
  statusMessage?: unknown;
}

const CLAUDE_POINT_MAP: Record<
  ClaudeHookPointName,
  "beforeToolUse" | "afterToolUse" | "beforeCommit"
> = {
  PreToolUse: "beforeToolUse",
  PostToolUse: "afterToolUse",
  PermissionRequest: "beforeToolUse",
  PermissionDenied: "afterToolUse",
  Stop: "beforeCommit",
};

export async function normalizeClaudeSkillHooks(
  opts: ClaudeSkillHookTrustOptions,
): Promise<{ hooks: SkillRuntimeHookDeclaration[]; issues: SkillRuntimeHookIssue[] }> {
  const { raw, skillName } = opts;
  if (raw === undefined || raw === null) return { hooks: [], issues: [] };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return {
      hooks: [],
      issues: [{ skillName, index: -1, reason: "`hooks` must be an object" }],
    };
  }

  const hooks: SkillRuntimeHookDeclaration[] = [];
  const issues: SkillRuntimeHookIssue[] = [];
  const rootObj = raw as Record<string, unknown>;

  for (const [pointName, point] of Object.entries(CLAUDE_POINT_MAP)) {
    const section = rootObj[pointName];
    if (section === undefined || section === null) continue;
    if (!Array.isArray(section)) {
      issues.push({
        skillName,
        index: -1,
        reason: `hooks.${pointName} must be an array`,
      });
      continue;
    }
    for (let i = 0; i < section.length; i++) {
      const item = section[i] as RawClaudeCommandHook;
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        issues.push({ skillName, index: i, reason: "hook must be an object" });
        continue;
      }
      const commandRaw =
        typeof item.command === "string" ? item.command.trim() : "";
      if (!commandRaw) {
        issues.push({ skillName, index: i, reason: "`command` is required" });
        continue;
      }
      const command = await resolveTrustedCommand({
        ...opts,
        commandRaw,
      });
      if ("issue" in command) {
        issues.push({ skillName, index: i, reason: command.issue });
        continue;
      }
      const timeoutMs =
        typeof item.timeout === "number" && Number.isFinite(item.timeout)
          ? Math.max(100, Math.min(60_000, item.timeout))
          : 5_000;
      const matcher =
        typeof item.matcher === "string" && item.matcher.trim()
          ? item.matcher.trim()
          : point === "beforeCommit"
            ? "beforeCommit"
            : "*";
      const statusMessage =
        typeof item.statusMessage === "string" && item.statusMessage.trim()
          ? item.statusMessage.trim()
          : undefined;
      hooks.push({
        skillName,
        name: `${pointName}-${i + 1}`,
        point,
        if: normalizeMatcher(point, matcher),
        action: "command",
        reason: statusMessage ?? `skill ${skillName} command hook`,
        priority: 55,
        blocking: true,
        timeoutMs,
        trustSource: command.trustSource,
        command: {
          path: command.path,
          skillRoot: command.skillRoot,
          timeoutMs,
          once: typeof item.once === "boolean" ? item.once : false,
          ...(statusMessage ? { statusMessage } : {}),
        },
      });
    }
  }

  return { hooks, issues };
}

export function registerSkillRuntimeHooks(
  registry: HookRegistry,
  declarations: SkillRuntimeHookDeclaration[],
): number {
  let registered = 0;
  for (const declaration of declarations) {
    registry.register(makeSkillRuntimeHook(declaration));
    registered++;
  }
  return registered;
}

function makeSkillRuntimeHook(
  declaration: SkillRuntimeHookDeclaration,
): RegisteredHook {
  let commandHasRun = false;
  const hook: RegisteredHook = {
    name: `skill:${declaration.skillName}:${declaration.name}`,
    point: declaration.point,
    priority: declaration.priority,
    blocking: declaration.blocking,
    if: declaration.if,
    timeoutMs: declaration.timeoutMs,
    handler: async (args, ctx) => {
      if (declaration.action === "command") {
        if (!declaration.command) {
          return {
            action: "block",
            reason: `[SKILL_RUNTIME_HOOK:${declaration.skillName}] command metadata missing`,
          };
        }
        if (declaration.command.once && commandHasRun) {
          return { action: "continue" };
        }
        commandHasRun = true;
        return runCommandHook(declaration, args, ctx);
      }
      if (declaration.action === "permission_decision") {
        return {
          action: "permission_decision",
          decision: declaration.decision ?? "ask",
          reason: declaration.reason,
        };
      }
      return {
        action: "block",
        reason: `[SKILL_RUNTIME_HOOK:${declaration.skillName}] ${declaration.reason}`,
      };
    },
  };
  return hook;
}

async function runCommandHook(
  declaration: SkillRuntimeHookDeclaration,
  args: unknown,
  ctx: Parameters<RegisteredHook["handler"]>[1],
): Promise<Awaited<ReturnType<RegisteredHook["handler"]>>> {
  const command = declaration.command!;
  const toolName = extractToolName(args);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result: Awaited<ReturnType<RegisteredHook["handler"]>>): void => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    const child = spawn(command.path, [], {
      cwd: command.skillRoot,
      env: {
        ...withClawyBinPath(process.env),
        CLAWY_SKILL_ROOT: command.skillRoot,
        CLAWY_HOOK_POINT: declaration.point,
        CLAWY_TOOL_NAME: toolName,
        CLAWY_TURN_ID: ctx.turnId,
        CLAWY_HOOK_ARGS: safeJsonStringify(args),
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const maxOut = 64 * 1024;
    child.stdout.on("data", (chunk: Buffer) => {
      if (stdout.length >= maxOut) return;
      stdout += chunk.toString("utf8").slice(0, maxOut - stdout.length);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      if (stderr.length >= maxOut) return;
      stderr += chunk.toString("utf8").slice(0, maxOut - stderr.length);
    });
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      finish({
        action: "block",
        reason: `[SKILL_RUNTIME_HOOK:${declaration.skillName}] command timed out`,
      });
    }, command.timeoutMs);
    timer.unref?.();
    ctx.abortSignal.addEventListener("abort", () => child.kill("SIGTERM"), {
      once: true,
    });
    child.on("error", (err) => {
      clearTimeout(timer);
      finish({
        action: "block",
        reason: `[SKILL_RUNTIME_HOOK:${declaration.skillName}] ${err.message}`,
      });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (settled) return;
      if (code === 0) {
        finish(parseCommandHookOutput(stdout));
        return;
      }
      const reason =
        command.statusMessage ??
        (stderr.trim() || stdout.trim() || `command exited ${String(code)}`);
      finish({
        action: "block",
        reason: `[SKILL_RUNTIME_HOOK:${declaration.skillName}] ${reason}`,
      });
    });
  });
}

function parseCommandHookOutput(
  stdout: string,
): Awaited<ReturnType<RegisteredHook["handler"]>> {
  const trimmed = stdout.trim();
  if (!trimmed) return { action: "continue" };
  try {
    const parsed = JSON.parse(trimmed) as {
      action?: unknown;
      decision?: unknown;
      reason?: unknown;
    };
    const reason =
      typeof parsed.reason === "string" ? parsed.reason : "skill command hook";
    if (parsed.action === "block") return { action: "block", reason };
    const decision =
      typeof parsed.decision === "string" && SUPPORTED_DECISIONS.has(parsed.decision)
        ? (parsed.decision as "approve" | "deny" | "ask")
        : undefined;
    if (parsed.action === "permission_decision" && decision) {
      return { action: "permission_decision", decision, reason };
    }
  } catch {
    // Plain stdout is informational; zero exit still means continue.
  }
  return { action: "continue" };
}

function extractToolName(args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const toolName = (args as { toolName?: unknown }).toolName;
  return typeof toolName === "string" ? toolName : "";
}

function normalizeMatcher(
  point: "beforeToolUse" | "afterToolUse" | "beforeCommit",
  matcher: string,
): string {
  const trimmed = matcher.trim();
  if (point === "beforeCommit") return trimmed || "beforeCommit";
  if (!trimmed || trimmed === "*") return "*";
  if (trimmed.includes("(") || trimmed.includes("*")) return trimmed;
  return `${trimmed}(*)`;
}

async function resolveTrustedCommand(opts: ClaudeSkillHookTrustOptions & {
  commandRaw: string;
}): Promise<
  | {
      path: string;
      skillRoot: string;
      trustSource: "trusted_root" | "admin_trusted" | "manifest";
    }
  | { issue: string }
> {
  const rel = normalizeCommandRel(opts.commandRaw);
  if ("issue" in rel) return rel;
  let realSkillRoot: string;
  let realWorkspaceRoot: string;
  let realCommand: string;
  try {
    realSkillRoot = await fs.realpath(opts.skillRoot);
    realWorkspaceRoot = await fs.realpath(opts.workspaceRoot);
    realCommand = await fs.realpath(path.join(realSkillRoot, rel.rel));
  } catch {
    return { issue: "hook command not found" };
  }
  if (!isPathInside(realCommand, realSkillRoot)) {
    return { issue: "hook command escapes skill root" };
  }

  const trustedRoot = await containingTrustedRoot(
    realSkillRoot,
    realWorkspaceRoot,
    opts.trustedSkillRoots ?? [],
  );
  if (trustedRoot) {
    return { path: realCommand, skillRoot: realSkillRoot, trustSource: "trusted_root" };
  }
  const trustedDir = await containingTrustedRoot(
    realSkillRoot,
    realWorkspaceRoot,
    opts.trustedSkillDirs ?? [],
  );
  if (trustedDir) {
    return { path: realCommand, skillRoot: realSkillRoot, trustSource: "admin_trusted" };
  }

  const manifest = await verifySkillManifest(realSkillRoot, ["SKILL.md", rel.rel]);
  if (manifest === true) {
    return { path: realCommand, skillRoot: realSkillRoot, trustSource: "manifest" };
  }
  return { issue: manifest ?? "untrusted command hook" };
}

function normalizeCommandRel(
  raw: string,
): { rel: string } | { issue: string } {
  if (/\s/.test(raw)) {
    return { issue: "hook command must be a script path without shell args" };
  }
  if (path.isAbsolute(raw)) {
    return { issue: "hook command must be relative to the skill root" };
  }
  const normalized = path.normalize(raw).replace(/^(\.\/)+/, "");
  if (
    normalized === ".." ||
    normalized.startsWith(`..${path.sep}`) ||
    normalized.split(path.sep).includes("..")
  ) {
    return { issue: "hook command parent traversal is not allowed" };
  }
  return { rel: normalized };
}

async function containingTrustedRoot(
  realSkillRoot: string,
  realWorkspaceRoot: string,
  roots: readonly string[],
): Promise<string | null> {
  for (const root of roots) {
    let realRoot: string;
    try {
      realRoot = await fs.realpath(root);
    } catch {
      continue;
    }
    if (isPathInside(realRoot, realWorkspaceRoot)) continue;
    if (isPathInside(realSkillRoot, realRoot)) return realRoot;
  }
  return null;
}

async function verifySkillManifest(
  realSkillRoot: string,
  relFiles: readonly string[],
): Promise<true | string> {
  const manifestPath = path.join(realSkillRoot, CLAWY_SKILL_MANIFEST);
  let parsed: unknown;
  try {
    parsed = JSON.parse(await fs.readFile(manifestPath, "utf8"));
  } catch {
    return "untrusted command hook";
  }
  if (!parsed || typeof parsed !== "object") return "invalid skill manifest";
  const files = (parsed as { files?: unknown }).files;
  if (!files || typeof files !== "object" || Array.isArray(files)) {
    return "invalid skill manifest";
  }
  for (const rel of relFiles) {
    const expected = manifestDigestFor((files as Record<string, unknown>)[rel]);
    if (!expected) return `manifest missing digest for ${rel}`;
    const actual = await sha256File(path.join(realSkillRoot, rel));
    if (expected !== actual) return `manifest digest mismatch for ${rel}`;
  }
  return true;
}

function manifestDigestFor(value: unknown): string | null {
  const raw =
    typeof value === "string"
      ? value
      : value && typeof value === "object" && typeof (value as { sha256?: unknown }).sha256 === "string"
        ? (value as { sha256: string }).sha256
        : null;
  if (!raw) return null;
  const digest = raw.startsWith("sha256:") ? raw.slice("sha256:".length) : raw;
  return /^[a-f0-9]{64}$/i.test(digest) ? digest.toLowerCase() : null;
}

async function sha256File(full: string): Promise<string> {
  return crypto.createHash("sha256").update(await fs.readFile(full)).digest("hex");
}

function isPathInside(child: string, parent: string): boolean {
  const rel = path.relative(parent, child);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

function safeJsonStringify(value: unknown): string {
  try {
    return JSON.stringify(value ?? {});
  } catch {
    return "{}";
  }
}
