/**
 * ArityPermissionPolicy — evaluate Bash commands against arity-based
 * permission rules.
 *
 * Rules use glob syntax matching the semantic prefix pattern:
 *   { pattern: "git push *", action: "ask" }
 *   { pattern: "kubectl delete *", action: "deny" }
 *
 * For multi-segment commands (pipes, &&, ||, ;), each segment is
 * evaluated independently and the most restrictive action wins.
 */

import { splitSegments, semanticPattern } from "./BashArity.js";

export interface ArityPermissionRule {
  pattern: string;
  action: "allow" | "ask" | "deny";
}

export interface ArityPermissionResult {
  action: "allow" | "ask" | "deny";
  matchedRule?: ArityPermissionRule;
  semanticPrefix: string;
}

const ACTION_RANK: Record<string, number> = { allow: 0, ask: 1, deny: 2 };

export const DEFAULT_ARITY_RULES: readonly ArityPermissionRule[] = [
  // Default: allow everything. Specific overrides below win (last match).
  { pattern: "* *", action: "allow" },
  { pattern: "git push *", action: "ask" },
  { pattern: "git reset *", action: "ask" },
  { pattern: "git checkout -- *", action: "deny" },
  { pattern: "git clean *", action: "deny" },
  { pattern: "rm *", action: "ask" },
  { pattern: "sudo *", action: "deny" },
  { pattern: "chmod *", action: "ask" },
  { pattern: "curl *", action: "ask" },
  { pattern: "wget *", action: "ask" },
  { pattern: "docker *", action: "ask" },
  { pattern: "kubectl *", action: "ask" },
  { pattern: "helm *", action: "ask" },
];

function globToRegExp(glob: string): RegExp {
  if (glob === "*") return /^.*$/s;
  let re = "^";
  for (let i = 0; i < glob.length; i++) {
    const ch = glob[i];
    if (ch === "*") {
      re += ".*";
    } else if (ch === "?") {
      re += ".";
    } else if (ch !== undefined && /[.+^$|()[\]{}\\]/.test(ch)) {
      re += "\\" + ch;
    } else {
      re += ch ?? "";
    }
  }
  re += "$";
  return new RegExp(re, "s");
}

/**
 * Evaluate a segment against the rule list. Rules are matched against
 * both the semantic prefix pattern (e.g. `git checkout *`) and the full
 * command string (e.g. `git checkout -- file.txt`). This allows rules
 * like `git checkout -- *` to match specific argument patterns while
 * general rules like `git push *` match the semantic prefix.
 * Last matching rule wins (allows overrides by appending rules).
 */
function evaluateSegment(
  segmentPattern: string,
  fullCommand: string,
  rules: readonly ArityPermissionRule[],
): { action: "allow" | "ask" | "deny"; matchedRule?: ArityPermissionRule } {
  let lastMatch: ArityPermissionRule | undefined;

  for (const rule of rules) {
    const re = globToRegExp(rule.pattern);
    if (re.test(segmentPattern) || re.test(fullCommand)) {
      lastMatch = rule;
    }
  }

  return lastMatch
    ? { action: lastMatch.action, matchedRule: lastMatch }
    : { action: "allow" };
}

/**
 * Evaluate a full command string against arity permission rules.
 *
 * 1. Parse into segments (handles pipes, &&, ||, ;)
 * 2. For each segment, compute semantic prefix pattern
 * 3. Match against rules — last match wins per segment
 * 4. Most restrictive action across all segments wins
 */
export function evaluateArityPermission(
  command: string,
  rules: readonly ArityPermissionRule[],
): ArityPermissionResult {
  const segments = splitSegments(command);
  if (segments.length === 0) {
    return { action: "allow", semanticPrefix: "" };
  }

  let worstAction: "allow" | "ask" | "deny" = "allow";
  let worstRule: ArityPermissionRule | undefined;
  const prefixes: string[] = [];

  for (const seg of segments) {
    const pattern = semanticPattern(seg);
    prefixes.push(pattern);
    const result = evaluateSegment(pattern, seg.raw, rules);

    if ((ACTION_RANK[result.action] ?? 0) > (ACTION_RANK[worstAction] ?? 0)) {
      worstAction = result.action;
      worstRule = result.matchedRule;
    }
  }

  return {
    action: worstAction,
    matchedRule: worstRule,
    semanticPrefix: prefixes.join(" | "),
  };
}
