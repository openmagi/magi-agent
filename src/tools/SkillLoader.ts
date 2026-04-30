/**
 * SkillLoader — parse workspace SKILL.md files into Tool instances.
 * Design reference: §9.8 P1, P4. Phase 2b: prompt-only skills (§9.8 P5).
 *
 * Each skill is a directory `workspace/skills/<name>/` with:
 *   - SKILL.md — YAML frontmatter + markdown body.
 *   - optional `<entry>` — an executable script the skill's tool_use
 *     dispatches to (e.g. `integration.sh`, `run.sh`).
 *
 * Two skill shapes supported:
 *
 *   1. **Script-backed** (Phase 2a, `kind: skill`): frontmatter declares
 *      `input_schema` + `entry`. Tool dispatches to a shell script; body
 *      is NEVER injected into prompts (CC-style).
 *
 *   2. **Prompt-only** (Phase 2b, `kind: prompt` or inferred when both
 *      `input_schema` and `entry` are absent): SKILL.md body IS the
 *      skill. The tool's single invocation returns the markdown body as
 *      tool_result content; the LLM then proceeds with that content in
 *      its context. Bodies larger than PROMPT_BODY_MAX_BYTES are
 *      truncated with a warning.
 *
 * Frontmatter schema (additive to legacy gateway shape):
 *
 *   name: string             — Tool name the model sees
 *   description: string      — 1-3 sentence, verb-first, ≤ 250 chars
 *   kind: "skill" | "prompt" — explicit opt-in (optional, inferred)
 *   input_schema: JSONSchema — REQUIRED for `kind: skill`
 *   entry: string            — REQUIRED for `kind: skill`
 *   permission: "read" | "write" | "execute" | "net" | "meta"
 *   dangerous: boolean       — default false
 *   tags: string[]           — intent tags (Phase 2b classifier)
 *   timeout_ms: number       — per-call timeout override (default 120s)
 *
 * Phase 2a contract preserved: a skill declared (implicitly or
 * explicitly) as `kind: skill` that lacks `input_schema` or `entry` is
 * still rejected — surfaced via report so /healthz can warn.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { parse as parseYaml } from "yaml";
import type { Tool, ToolContext, ToolResult, PermissionClass } from "../Tool.js";
import { errorResult } from "../util/toolResult.js";
import { withClawyBinPath } from "../util/shellPath.js";
import {
  normalizeClaudeSkillHooks,
  normalizeSkillRuntimeHooks,
  type SkillRuntimeHookDeclaration,
} from "./SkillRuntimeHooks.js";

export interface SkillFrontmatter {
  name?: string;
  description?: string;
  user_invocable?: boolean;
  /** Explicit skill shape. When absent, inferred from input_schema/entry. */
  kind?: "skill" | "prompt";
  input_schema?: object;
  entry?: string;
  permission?: PermissionClass;
  dangerous?: boolean;
  tags?: string[];
  timeout_ms?: number;
  runtime_hooks?: unknown;
  hooks?: unknown;
}

export interface SkillLoadIssue {
  dir: string;
  skillName?: string;
  reason:
    | "no_skill_md"
    | "frontmatter_missing"
    | "frontmatter_parse_error"
    | "missing_name"
    | "missing_description"
    | "missing_input_schema"
    | "description_too_long"
    | "description_not_verb"
    | "entry_not_found"
    | "runtime_hook_invalid";
  detail?: string;
}

export interface SkillLoadReport {
  loaded: Array<{
    name: string;
    scriptBacked: boolean;
    promptOnly?: boolean;
    tags: string[];
    runtimeHooks: number;
  }>;
  issues: SkillLoadIssue[];
  runtimeHooks: SkillRuntimeHookDeclaration[];
}

/**
 * Max byte size of a prompt-only skill body injected into the LLM
 * context. Bodies above this are truncated with a trailing marker to
 * keep per-turn token budget predictable. 20 KB ≈ 5–6k tokens.
 */
export const PROMPT_BODY_MAX_BYTES = 20 * 1024;

/**
 * Parse a SKILL.md file into (frontmatter, body). Returns null if the
 * file has no frontmatter delimiter.
 */
export function parseSkillMd(raw: string): {
  frontmatter: SkillFrontmatter;
  body: string;
} | null {
  if (!raw.startsWith("---\n") && !raw.startsWith("---\r\n")) return null;
  const rest = raw.slice(raw.indexOf("\n") + 1);
  const endDelim = rest.indexOf("\n---");
  if (endDelim < 0) return null;
  const yaml = rest.slice(0, endDelim);
  const body = rest.slice(endDelim + 4).replace(/^\r?\n/, "");
  try {
    const frontmatter = (parseYaml(yaml) ?? {}) as SkillFrontmatter;
    return { frontmatter, body };
  } catch {
    return null;
  }
}

/**
 * Description must be verb-first (heuristic: first token isn't a
 * sentence-starting article). P4 of §9.8.
 */
function startsWithVerb(desc: string): boolean {
  const first = desc.trim().split(/\s+/)[0];
  if (!first) return false;
  const articles = new Set(["the", "a", "an", "this", "that", "these", "those", "it"]);
  return !articles.has(first.toLowerCase());
}

async function realpathIfAvailable(p: string): Promise<string> {
  const resolved = path.resolve(p);
  try {
    return await fs.realpath(resolved);
  } catch {
    return resolved;
  }
}

/**
 * Build a Tool for a script-backed skill. Phase 2a dispatch: spawn
 * /bin/sh -c <entry> with the JSON input piped on stdin. Skills can
 * also read `CLAWY_SKILL_INPUT` from the environment. stdout is the
 * tool_result content; non-zero exit is treated as an error.
 */
function makeScriptSkillTool(opts: {
  skillName: string;
  skillDir: string;
  fm: SkillFrontmatter;
  entry: string;
  workspaceRoot: string;
}): Tool<unknown, unknown> {
  const { skillName, skillDir, fm, entry, workspaceRoot } = opts;
  const timeoutMs = Math.min(600_000, fm.timeout_ms ?? 120_000);
  const permission: PermissionClass = fm.permission ?? "execute";

  return {
    name: skillName,
    description: (fm.description ?? "").slice(0, 250),
    inputSchema: normalizeToolInputSchema(fm.input_schema),
    permission,
    kind: "skill",
    tags: fm.tags ?? [],
    ...(fm.dangerous ? { dangerous: true } : {}),
    async execute(input: unknown, ctx: ToolContext): Promise<ToolResult<unknown>> {
      const start = Date.now();
      const inputJson = safeJsonStringify(input);
      // Resolve entry relative to the skill's directory first; fall
      // back to workspace-relative if that's how the author wrote it.
      const candidates = [
        path.join(skillDir, entry),
        path.join(workspaceRoot, entry),
      ];
      let resolvedEntry: string | null = null;
      for (const c of candidates) {
        try {
          await fs.access(c);
          resolvedEntry = c;
          break;
        } catch {
          /* next */
        }
      }
      if (!resolvedEntry) {
        return {
          status: "error",
          errorCode: "entry_not_found",
          errorMessage: `skill entry script not found: ${entry}`,
          durationMs: Date.now() - start,
        };
      }
      const effectiveWorkspaceRoot = await realpathIfAvailable(
        ctx.spawnWorkspace?.root ?? ctx.workspaceRoot,
      );

      return new Promise<ToolResult<unknown>>((resolve) => {
        try {
          const child = spawn("/bin/sh", ["-c", resolvedEntry!], {
            cwd: effectiveWorkspaceRoot,
            env: {
              ...withClawyBinPath(process.env),
              PWD: effectiveWorkspaceRoot,
              CLAWY_WORKSPACE_ROOT: effectiveWorkspaceRoot,
              CLAWY_SKILL_INPUT: inputJson,
              CLAWY_SKILL_NAME: skillName,
              CLAWY_BOT_ID: ctx.botId,
              CLAWY_SESSION_KEY: ctx.sessionKey,
              CLAWY_TURN_ID: ctx.turnId,
            },
            stdio: ["pipe", "pipe", "pipe"],
          });

          let stdout = "";
          let stderr = "";
          const MAX_OUT = 512 * 1024;
          let truncated = false;
          child.stdout.on("data", (c: Buffer) => {
            if (stdout.length >= MAX_OUT) {
              truncated = true;
              return;
            }
            stdout += c.toString("utf8").slice(0, MAX_OUT - stdout.length);
          });
          child.stderr.on("data", (c: Buffer) => {
            if (stderr.length >= MAX_OUT) return;
            stderr += c.toString("utf8").slice(0, MAX_OUT - stderr.length);
          });
          child.stdin.on("error", () => {
            /* ignore EPIPE if script closes stdin early */
          });
          child.stdin.end(inputJson);

          const timer = setTimeout(() => {
            child.kill("SIGTERM");
            setTimeout(() => child.kill("SIGKILL"), 3_000).unref();
          }, timeoutMs);
          ctx.abortSignal.addEventListener("abort", () => child.kill("SIGTERM"), {
            once: true,
          });

          child.on("close", (code) => {
            clearTimeout(timer);
            const ok = code === 0;
            resolve({
              status: ok ? "ok" : "error",
              output: ok ? parseToolOutput(stdout) : undefined,
              errorCode: ok ? undefined : `exit_${code}`,
              errorMessage: ok
                ? undefined
                : (stderr || stdout).slice(0, 500) || `exit ${code}`,
              durationMs: Date.now() - start,
              metadata: { truncated, stderr: stderr.slice(0, 1024) },
            });
          });
          child.on("error", (err) => {
            clearTimeout(timer);
            resolve(errorResult(err, start));
          });
        } catch (err) {
          resolve(errorResult(err, start));
        }
      });
    },
  };
}

/**
 * Build a Tool for a prompt-only skill (Phase 2b). The tool is a thin
 * wrapper whose single invocation returns the SKILL.md body as
 * tool_result content — no script execution, no side-effects. The LLM
 * calls it (selected by intent tags in the classifier pass), reads the
 * content, and proceeds with the skill context loaded.
 *
 * Design choice: prompt-only skills piggyback on the existing Tool
 * interface rather than introducing a new "prompt fragment" registry.
 * This keeps the Turn dispatch, permission gate, and intent-filter
 * paths identical across both shapes — the only difference is what
 * `execute()` returns. The `inputSchema` is a permissive empty object
 * so the LLM can invoke with `{}` (or any extra hints it wants to
 * pass). `permission: "meta"` because we do no I/O.
 */
function makePromptSkillTool(opts: {
  skillName: string;
  fm: SkillFrontmatter;
  body: string;
}): Tool<unknown, { content: string; truncated: boolean }> {
  const { skillName, fm, body } = opts;
  const { text, truncated } = truncatePromptBody(body);
  if (truncated) {
    console.warn(
      `[core-agent] skill "${skillName}" body truncated to ${PROMPT_BODY_MAX_BYTES} bytes for prompt-only delivery`,
    );
  }

  return {
    name: skillName,
    description: (fm.description ?? "").slice(0, 250),
    // Permissive schema — prompt-only tools take no required input. The
    // LLM may pass `{}` or any payload; we ignore it.
    inputSchema: normalizeToolInputSchema(fm.input_schema),
    permission: fm.permission ?? "meta",
    kind: "skill",
    tags: fm.tags ?? [],
    async execute(): Promise<ToolResult<{ content: string; truncated: boolean }>> {
      const start = Date.now();
      return {
        status: "ok",
        output: { content: text, truncated },
        durationMs: Date.now() - start,
        metadata: { promptOnly: true, truncated },
      };
    },
  };
}

/** Byte-accurate truncation that falls back to UTF-8 safe slicing. */
function truncatePromptBody(body: string): {
  text: string;
  truncated: boolean;
} {
  const buf = Buffer.from(body, "utf8");
  if (buf.length <= PROMPT_BODY_MAX_BYTES) {
    return { text: body, truncated: false };
  }
  // Slice by bytes then re-decode; Node's utf8 decoder inserts the
  // replacement char for any split codepoint, which is fine for a
  // truncation marker.
  const sliced = buf.subarray(0, PROMPT_BODY_MAX_BYTES).toString("utf8");
  return {
    text: `${sliced}\n\n[...TRUNCATED at ${PROMPT_BODY_MAX_BYTES} bytes]`,
    truncated: true,
  };
}

function safeJsonStringify(v: unknown): string {
  try {
    return JSON.stringify(v ?? {});
  } catch {
    return "{}";
  }
}

function normalizeToolInputSchema(schema: unknown): object {
  const base =
    schema && typeof schema === "object" && !Array.isArray(schema)
      ? schema
      : { type: "object", additionalProperties: true };
  return normalizeSchemaNode(base) as object;
}

function normalizeSchemaNode(schema: unknown): unknown {
  if (Array.isArray(schema)) {
    return schema.map((item) => normalizeSchemaNode(item));
  }
  if (!schema || typeof schema !== "object") {
    return schema;
  }

  const node: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(schema)) {
    node[key] = normalizeSchemaNode(value);
  }

  if (node["type"] === "object" && !("properties" in node)) {
    node["properties"] = {};
  }
  if (node["type"] === "array" && !("items" in node)) {
    node["items"] = {};
  }

  return node;
}

/**
 * Parse tool output — prefer JSON so the LLM sees structured data; if
 * the script emitted plain text, wrap it in { output: "…" }.
 */
function parseToolOutput(raw: string): unknown {
  const trimmed = raw.trim();
  if (!trimmed) return { output: "" };
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return JSON.parse(trimmed);
    } catch {
      /* fall through */
    }
  }
  return { output: trimmed };
}

/**
 * Walk each skill directory under `skillsDir`, validate SKILL.md, and
 * build Tools. Caller registers them with ToolRegistry. Issues are
 * returned (not thrown) so a single malformed skill doesn't break
 * startup.
 */
export async function loadSkillsFromDir(opts: {
  skillsDir: string;
  workspaceRoot: string;
  trustedSkillRoots?: readonly string[];
  trustedSkillDirs?: readonly string[];
}): Promise<{ tools: Tool[]; report: SkillLoadReport }> {
  const { skillsDir, workspaceRoot } = opts;
  const report: SkillLoadReport = { loaded: [], issues: [], runtimeHooks: [] };
  const tools: Tool[] = [];

  let entries: string[];
  try {
    entries = await fs.readdir(skillsDir);
  } catch {
    return { tools, report };
  }

  for (const entry of entries) {
    const dir = path.join(skillsDir, entry);
    let stat;
    try {
      stat = await fs.stat(dir);
    } catch {
      continue;
    }
    if (!stat.isDirectory()) continue;

    const skillMdPath = path.join(dir, "SKILL.md");
    let raw: string;
    try {
      raw = await fs.readFile(skillMdPath, "utf8");
    } catch {
      report.issues.push({ dir: entry, reason: "no_skill_md" });
      continue;
    }

    const parsed = parseSkillMd(raw);
    if (!parsed) {
      report.issues.push({ dir: entry, reason: "frontmatter_parse_error" });
      continue;
    }
    const { frontmatter: fm, body } = parsed;

    const skillName = fm.name?.trim();
    if (!skillName) {
      report.issues.push({ dir: entry, reason: "missing_name" });
      continue;
    }
    const description = fm.description?.trim();
    if (!description) {
      report.issues.push({ dir: entry, skillName, reason: "missing_description" });
      continue;
    }
    if (description.length > 250) {
      report.issues.push({
        dir: entry,
        skillName,
        reason: "description_too_long",
        detail: `${description.length} chars`,
      });
      continue;
    }
    if (!startsWithVerb(description)) {
      report.issues.push({
        dir: entry,
        skillName,
        reason: "description_not_verb",
      });
      continue;
    }

    const runtimeHookResult = normalizeSkillRuntimeHooks(
      skillName,
      fm.runtime_hooks,
    );
    const claudeHookResult = await normalizeClaudeSkillHooks({
      skillName,
      skillRoot: dir,
      workspaceRoot,
      raw: fm.hooks,
      trustedSkillRoots: opts.trustedSkillRoots,
      trustedSkillDirs: opts.trustedSkillDirs,
    });
    for (const issue of runtimeHookResult.issues) {
      report.issues.push({
        dir: entry,
        skillName,
        reason: "runtime_hook_invalid",
        detail:
          issue.index >= 0
            ? `runtime_hooks[${issue.index}]: ${issue.reason}`
            : issue.reason,
      });
    }
    for (const issue of claudeHookResult.issues) {
      report.issues.push({
        dir: entry,
        skillName,
        reason: "runtime_hook_invalid",
        detail:
          issue.index >= 0
            ? `hooks[${issue.index}]: ${issue.reason}`
            : issue.reason,
      });
    }
    const validRuntimeHooks = [
      ...runtimeHookResult.hooks,
      ...claudeHookResult.hooks,
    ];
    // Phase 2b shape detection. Explicit `kind: prompt` always wins.
    // Otherwise infer: a skill with neither `input_schema` nor `entry`
    // is prompt-only (the 138 bundled legacy gateway-style skills). A skill
    // declaring `kind: skill` — or implying it by having `entry` OR
    // `input_schema` — must satisfy the Phase 2a contract.
    const explicitPrompt = fm.kind === "prompt";
    const explicitSkill = fm.kind === "skill";
    const isPromptOnly =
      explicitPrompt ||
      (!explicitSkill && !fm.input_schema && !fm.entry);

    if (isPromptOnly) {
      const tool = makePromptSkillTool({ skillName, fm, body });
      tools.push(tool);
      report.runtimeHooks.push(...validRuntimeHooks);
      report.loaded.push({
        name: skillName,
        scriptBacked: false,
        promptOnly: true,
        tags: fm.tags ?? [],
        runtimeHooks: validRuntimeHooks.length,
      });
      continue;
    }

    // Script-backed (Phase 2a) — enforce full contract.
    if (!fm.input_schema) {
      report.issues.push({
        dir: entry,
        skillName,
        reason: "missing_input_schema",
      });
      continue;
    }
    if (!fm.entry) {
      report.issues.push({
        dir: entry,
        skillName,
        reason: "entry_not_found",
        detail: "script-backed skill declared without `entry`",
      });
      continue;
    }

    const tool = makeScriptSkillTool({
      skillName,
      skillDir: dir,
      fm,
      entry: fm.entry,
      workspaceRoot,
    });
    tools.push(tool);
    report.runtimeHooks.push(...validRuntimeHooks);
    report.loaded.push({
      name: skillName,
      scriptBacked: true,
      tags: fm.tags ?? [],
      runtimeHooks: validRuntimeHooks.length,
    });
  }

  return { tools, report };
}
