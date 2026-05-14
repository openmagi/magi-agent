/**
 * Built-in arity-based permission gate — beforeToolUse hook at priority 38.
 *
 * Semantic command classification via BashArity prefix extraction +
 * glob-based permission rules. Runs after auto-approval (30) and before
 * dangerousPatterns (40) so both layers can coexist.
 *
 * Config shape (agent.config.yaml):
 *   arity_rules:
 *     - pattern: "git push *"
 *       action: "ask"
 *     - pattern: "docker compose *"
 *       action: "allow"
 *
 * Env gate: MAGI_ARITY_PERMISSION (default: off during rollout).
 */

import fs from "node:fs/promises";
import path from "node:path";
import { parse as parseYaml } from "yaml";
import type { RegisteredHook, HookContext } from "../types.js";
import {
  evaluateArityPermission,
  DEFAULT_ARITY_RULES,
  type ArityPermissionRule,
} from "../../security/ArityPermissionPolicy.js";

const CONFIG_REL = "agent.config.yaml";

export function isArityPermissionEnabled(): boolean {
  const raw = process.env.MAGI_ARITY_PERMISSION;
  if (raw === undefined || raw === null) return false;
  const v = raw.trim().toLowerCase();
  if (v === "on" || v === "true" || v === "1") return true;
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

export function resolveArityRulesFromConfig(
  config: Record<string, unknown> | null,
): ArityPermissionRule[] | null {
  if (!config) return null;
  if (!Object.prototype.hasOwnProperty.call(config, "arity_rules")) {
    return null;
  }
  const raw = config["arity_rules"];
  if (!Array.isArray(raw)) return null;

  const out: ArityPermissionRule[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const e = entry as Record<string, unknown>;
    const pattern = typeof e["pattern"] === "string" ? (e["pattern"] as string) : "";
    if (!pattern) continue;
    const actionRaw = e["action"];
    let action: "allow" | "ask" | "deny" = "ask";
    if (actionRaw === "allow") action = "allow";
    else if (actionRaw === "deny") action = "deny";
    out.push({ pattern, action });
  }
  return out;
}

export interface ArityPermissionGateOptions {
  workspaceRoot: string;
}

export function makeArityPermissionGateHook(
  opts: ArityPermissionGateOptions,
): RegisteredHook<"beforeToolUse"> {
  return {
    name: "builtin:arity-permission-gate",
    point: "beforeToolUse",
    priority: 38,
    blocking: true,
    timeoutMs: 3_000,
    handler: async ({ toolName, input }, ctx: HookContext) => {
      if (!isArityPermissionEnabled()) return { action: "continue" };
      if (toolName !== "Bash") return { action: "continue" };

      if (!input || typeof input !== "object") return { action: "continue" };
      const obj = input as Record<string, unknown>;
      const command = obj["command"];
      if (typeof command !== "string" || !command.trim()) {
        return { action: "continue" };
      }

      const config = await readConfig(opts.workspaceRoot);
      const fromConfig = resolveArityRulesFromConfig(config);
      const rules: readonly ArityPermissionRule[] =
        fromConfig !== null ? fromConfig : DEFAULT_ARITY_RULES;

      const result = evaluateArityPermission(command, rules);

      if (result.action === "allow") {
        return { action: "continue" };
      }

      ctx.emit({
        type: "rule_check",
        ruleId: "arity-permission-gate",
        verdict: "violation",
        detail: `arity_permission action=${result.action} prefix=${result.semanticPrefix} rule=${result.matchedRule?.pattern ?? "none"} tool=${toolName}`,
      });
      ctx.log("warn", "[arityPermissionGate] command gated", {
        turnId: ctx.turnId,
        toolName,
        action: result.action,
        semanticPrefix: result.semanticPrefix,
        matchedPattern: result.matchedRule?.pattern,
      });

      if (result.action === "deny") {
        return {
          action: "permission_decision",
          decision: "deny",
          reason: `[ARITY_DENY] Command "${result.semanticPrefix}" is denied by arity permission policy (rule: ${result.matchedRule?.pattern ?? "default"})`,
        };
      }

      return {
        action: "permission_decision",
        decision: "ask",
        reason: `Command "${result.semanticPrefix}" requires approval (rule: ${result.matchedRule?.pattern ?? "default"}). Allow Bash to proceed?`,
      };
    },
  };
}
