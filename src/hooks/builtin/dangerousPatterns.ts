/**
 * Built-in dangerous-patterns hook — T2-09 (Phase 3).
 *
 * Design reference:
 * - `docs/plans/2026-04-19-core-agent-phase-3-plan.md` §4 / T2-09
 * - `docs/notes/2026-04-19-cc-parity-audit-03-permissions-plan-hooks-tasks.md` P2
 *
 * Declarative allowlist/asklist of dangerous Bash commands and file
 * paths. Reads `workspace/agent.config.yaml → dangerous_patterns: [...]`;
 * when the `dangerous_patterns` key is absent, a hardcoded default list
 * is used so a fresh bot still gets baseline protection.
 *
 * The hook is a `beforeToolUse` handler running at priority 40 — after
 * the (future) auto-approval hook at 30, before selfClaimVerifier at 80.
 * On each tool call it determines the relevant scope (`bash` for Bash,
 * `path` for file tools), extracts the target string, and tests every
 * matching rule. A match yields a T2-07 `permission_decision`:
 *   - `action: "deny"` → decision "deny" with a tagged reason.
 *   - `action: "ask"` (default) → decision "ask"; HookRegistry prompts
 *     the user via ctx.askUser.
 *
 * Rule shape (YAML):
 *   dangerous_patterns:
 *     - match: "rm -rf /"
 *       scope: "bash"         # "bash" | "path"
 *       kind:  "regex"        # "substring" (default) | "regex"
 *       action: "ask"         # "ask" (default) | "deny"
 *
 * Rules with an invalid `kind: regex` pattern are skipped (logged once
 * at load time) rather than crashing the hook.
 *
 * Toggle:
 *   CORE_AGENT_DANGEROUS_PATTERNS=off  — disable globally.
 *   disable_builtin_hooks: [builtin:dangerous-patterns]  — disable per-bot.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { parse as parseYaml } from "yaml";
import type { RegisteredHook, HookContext } from "../types.js";

export type DangerousPatternScope = "bash" | "path";
export type DangerousPatternKind = "substring" | "regex";
export type DangerousPatternAction = "ask" | "deny";

export interface DangerousPatternRule {
  match: string;
  scope: DangerousPatternScope;
  kind?: DangerousPatternKind;
  action?: DangerousPatternAction;
}

export const DEFAULT_DANGEROUS_PATTERNS: readonly DangerousPatternRule[] = [
  { match: "rm -rf /", scope: "bash" },
  { match: "rm -rf ~", scope: "bash" },
  { match: "^\\.env", scope: "path", kind: "regex" },
  { match: "secrets/", scope: "path" },
  { match: "sudo", scope: "bash" },
  { match: "chmod 777", scope: "bash" },
  { match: "curl.*\\|.*sh", scope: "bash", kind: "regex" },
  { match: "\\bgit\\s+push\\b", scope: "bash", kind: "regex", action: "ask" },
  { match: "\\bgit\\s+reset\\s+--hard\\b", scope: "bash", kind: "regex", action: "deny" },
  { match: "\\bgit\\s+checkout\\s+--\\b", scope: "bash", kind: "regex", action: "deny" },
  { match: "(?:^|\\s)(?:env|printenv)(?:\\s|$)", scope: "bash", kind: "regex", action: "ask" },
  { match: "^\\.ssh", scope: "path", kind: "regex", action: "deny" },
];

const CONFIG_REL = "agent.config.yaml";

const PATH_TOOLS = new Set<string>(["FileWrite", "FileEdit", "FileRead"]);

export function isEnabledByEnv(): boolean {
  const raw = process.env.CORE_AGENT_DANGEROUS_PATTERNS;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  if (v === "" || v === "on" || v === "true" || v === "1") return true;
  return false;
}

async function readConfig(
  workspaceRoot: string,
): Promise<Record<string, unknown> | null> {
  const configPath = path.join(workspaceRoot, CONFIG_REL);
  let raw: string;
  try {
    raw = await fs.readFile(configPath, "utf8");
  } catch {
    return null;
  }
  try {
    const parsed = parseYaml(raw);
    if (parsed && typeof parsed === "object") {
      return parsed as Record<string, unknown>;
    }
  } catch {
    return null;
  }
  return null;
}

/**
 * Extract and sanitise the `dangerous_patterns` list off the parsed
 * config. Returns:
 *   - `null` when the key is absent (caller should fall back to defaults).
 *   - `[]` (empty array) when the key is present but an explicit empty
 *     list — this is an intentional operator override meaning "no rules".
 *   - filtered `DangerousPatternRule[]` otherwise.
 */
export function resolveRulesFromConfig(
  config: Record<string, unknown> | null,
): DangerousPatternRule[] | null {
  if (!config) return null;
  if (!Object.prototype.hasOwnProperty.call(config, "dangerous_patterns")) {
    return null;
  }
  const raw = config["dangerous_patterns"];
  if (!Array.isArray(raw)) return null;
  const out: DangerousPatternRule[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const e = entry as Record<string, unknown>;
    const match = typeof e["match"] === "string" ? (e["match"] as string) : "";
    const scope = e["scope"];
    if (!match) continue;
    if (scope !== "bash" && scope !== "path") continue;
    const kindRaw = e["kind"];
    const kind: DangerousPatternKind =
      kindRaw === "regex" ? "regex" : "substring";
    const actionRaw = e["action"];
    const action: DangerousPatternAction =
      actionRaw === "deny" ? "deny" : "ask";
    out.push({ match, scope, kind, action });
  }
  return out;
}

interface CompiledRule {
  rule: DangerousPatternRule;
  test: (target: string) => boolean;
}

/**
 * Compile a rule into a predicate. On regex compilation failure the
 * rule is dropped; the caller logs once. Substring matching is
 * case-sensitive (matches `rm -rf /` literally).
 */
function compileRule(
  rule: DangerousPatternRule,
  onError: (err: unknown) => void,
): CompiledRule | null {
  const kind: DangerousPatternKind = rule.kind ?? "substring";
  if (kind === "regex") {
    let re: RegExp;
    try {
      re = new RegExp(rule.match);
    } catch (err) {
      onError(err);
      return null;
    }
    return {
      rule,
      test: (target: string) => re.test(target),
    };
  }
  const needle = rule.match;
  return {
    rule,
    test: (target: string) => target.includes(needle),
  };
}

function extractTarget(
  toolName: string,
  input: unknown,
): { scope: DangerousPatternScope; target: string } | null {
  if (!input || typeof input !== "object") return null;
  const obj = input as Record<string, unknown>;
  if (toolName === "Bash") {
    const cmd = obj["command"];
    if (typeof cmd === "string") {
      return { scope: "bash", target: cmd };
    }
    return null;
  }
  if (PATH_TOOLS.has(toolName)) {
    const p = obj["path"];
    if (typeof p === "string") {
      return { scope: "path", target: p };
    }
    return null;
  }
  return null;
}

export interface DangerousPatternsOptions {
  workspaceRoot: string;
}

export function makeDangerousPatternsHook(
  opts: DangerousPatternsOptions,
): RegisteredHook<"beforeToolUse"> {
  return {
    name: "builtin:dangerous-patterns",
    point: "beforeToolUse",
    priority: 40,
    blocking: true,
    timeoutMs: 3_000,
    handler: async ({ toolName, input }, ctx: HookContext) => {
      if (!isEnabledByEnv()) return { action: "continue" };

      const scoped = extractTarget(toolName, input);
      if (!scoped) return { action: "continue" };

      const config = await readConfig(opts.workspaceRoot);
      const fromConfig = resolveRulesFromConfig(config);
      const rules: readonly DangerousPatternRule[] =
        fromConfig === null ? DEFAULT_DANGEROUS_PATTERNS : fromConfig;
      if (rules.length === 0) return { action: "continue" };

      const compileErrors: string[] = [];
      const compiled: CompiledRule[] = [];
      for (const r of rules) {
        if (r.scope !== scoped.scope) continue;
        const c = compileRule(r, (err) => {
          compileErrors.push(`${r.match}: ${String(err)}`);
        });
        if (c) compiled.push(c);
      }
      if (compileErrors.length > 0) {
        ctx.log("warn", "[dangerousPatterns] invalid regex rule(s) skipped", {
          errors: compileErrors,
        });
      }

      for (const { rule, test } of compiled) {
        if (!test(scoped.target)) continue;

        const action: DangerousPatternAction = rule.action ?? "ask";

        ctx.emit({
          type: "rule_check",
          ruleId: "dangerous-patterns",
          verdict: "violation",
          detail: `dangerous_pattern_matched rule=${rule.match} scope=${rule.scope} kind=${rule.kind ?? "substring"} action=${action} tool=${toolName}`,
        });
        ctx.log("warn", "[dangerousPatterns] pattern matched", {
          turnId: ctx.turnId,
          toolName,
          rule: rule.match,
          scope: rule.scope,
          kind: rule.kind ?? "substring",
          action,
        });

        if (action === "deny") {
          return {
            action: "permission_decision",
            decision: "deny",
            reason: `[DANGEROUS_PATTERN] matched: ${rule.match}`,
          };
        }
        return {
          action: "permission_decision",
          decision: "ask",
          reason: `Dangerous pattern matched (${rule.scope}): "${rule.match}". Allow ${toolName} to proceed?`,
        };
      }

      return { action: "continue" };
    },
  };
}
