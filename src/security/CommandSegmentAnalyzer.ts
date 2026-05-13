/**
 * Per-segment command analysis — P1 of the security analysis pipeline.
 *
 * Parses shell commands into pipe segments respecting quotes/heredocs,
 * then evaluates combination rules against segment pairs. Falls back
 * to legacy flat regex matching for backward compatibility.
 */

import type { DangerousPatternRule } from "../hooks/builtin/dangerousPatterns.js";

export interface PipeSegment {
  command: string;
  args: string[];
  raw: string;
  connector: string | null;
}

export interface SegmentRule {
  id: string;
  match: {
    left: RegExp;
    right: RegExp;
    connector: "|";
  };
  action: "ask" | "deny";
  severity: "high" | "critical";
  description: string;
  /** For single-segment rules that don't need a pair */
  singleSegment?: {
    pattern: RegExp;
  };
}

export interface SegmentAnalysisResult {
  segments: PipeSegment[];
  violations: Array<{
    rule: SegmentRule;
    leftSegment: PipeSegment;
    rightSegment: PipeSegment;
  }>;
  legacyMatches: Array<{ rule: DangerousPatternRule; target: string }>;
}

type Connector = "|" | "&&" | "||" | ";";

const CONNECTORS: readonly Connector[] = ["||", "&&", "|", ";"];

export function parseCommandSegments(command: string): PipeSegment[] {
  const trimmed = command.trim();
  if (!trimmed) return [];

  const rawSegments: Array<{ raw: string; connector: Connector | null }> = [];
  let current = "";
  let inSingle = false;
  let inDouble = false;
  let escaped = false;

  let i = 0;
  while (i < trimmed.length) {
    const ch = trimmed[i];

    if (escaped) {
      current += ch;
      escaped = false;
      i++;
      continue;
    }

    if (ch === "\\") {
      escaped = true;
      current += ch;
      i++;
      continue;
    }

    if (ch === "'" && !inDouble) {
      inSingle = !inSingle;
      current += ch;
      i++;
      continue;
    }

    if (ch === '"' && !inSingle) {
      inDouble = !inDouble;
      current += ch;
      i++;
      continue;
    }

    if (inSingle || inDouble) {
      current += ch;
      i++;
      continue;
    }

    let matched: Connector | null = null;
    for (const conn of CONNECTORS) {
      if (trimmed.startsWith(conn, i)) {
        matched = conn;
        break;
      }
    }

    if (matched) {
      rawSegments.push({ raw: current, connector: matched });
      current = "";
      i += matched.length;
      continue;
    }

    current += ch;
    i++;
  }

  if (current.trim()) {
    rawSegments.push({ raw: current, connector: null });
  }

  return rawSegments
    .map(({ raw, connector }) => {
      const tokens = tokenize(raw.trim());
      if (tokens.length === 0) return null;
      const command = tokens[0];
      if (!command) return null;
      return {
        command,
        args: tokens.slice(1),
        raw,
        connector,
      };
    })
    .filter((s): s is NonNullable<typeof s> => s !== null);
}

function tokenize(segment: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let inSingle = false;
  let inDouble = false;
  let escaped = false;

  for (let i = 0; i < segment.length; i++) {
    const ch = segment[i];

    if (escaped) {
      current += ch;
      escaped = false;
      continue;
    }

    if (ch === "\\") {
      escaped = true;
      continue;
    }

    if (ch === "'" && !inDouble) {
      inSingle = !inSingle;
      continue;
    }

    if (ch === '"' && !inSingle) {
      inDouble = !inDouble;
      continue;
    }

    if ((ch === " " || ch === "\t") && !inSingle && !inDouble) {
      if (current) {
        tokens.push(current);
        current = "";
      }
      continue;
    }

    current += ch;
  }

  if (current) tokens.push(current);
  return tokens;
}

export const DEFAULT_SEGMENT_RULES: readonly SegmentRule[] = [
  {
    id: "curl_pipe_exec",
    match: {
      left: /^(curl|wget)$/,
      right: /^(sh|bash|zsh|dash|python|python3|node|perl|ruby|eval)$/,
      connector: "|",
    },
    action: "deny",
    severity: "critical",
    description: "Network download piped to shell interpreter",
  },
  {
    id: "download_pipe_exec",
    match: {
      left: /^(curl|wget|fetch|nc|ncat|socat)$/,
      right: /^(sh|bash|zsh|dash|python|python3|node|perl|ruby|eval|exec|source|\.)$/,
      connector: "|",
    },
    action: "deny",
    severity: "high",
    description: "Network fetch piped to code interpreter",
  },
  {
    id: "recursive_delete",
    match: { left: /^$/, right: /^$/, connector: "|" },
    action: "deny",
    severity: "critical",
    description: "Recursive forced deletion of root or home directory",
    singleSegment: {
      pattern: /^rm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*|--recursive\s+--force|--force\s+--recursive)\s+(\/\s*$|~\s*$|\/\s|~\s)/,
    },
  },
  {
    id: "chmod_world_write",
    match: { left: /^$/, right: /^$/, connector: "|" },
    action: "ask",
    severity: "high",
    description: "Setting world-writable permissions on sensitive paths",
    singleSegment: {
      pattern: /^chmod\s+(?:777|o\+w)\s+\/(etc|usr|var|bin|sbin|boot|root|home|sys|proc)/,
    },
  },
  {
    id: "env_dump",
    match: {
      left: /^(env|printenv|set)$/,
      right: /^(curl|wget|nc|ncat|socat|telnet|ssh|scp|ftp|sftp)$/,
      connector: "|",
    },
    action: "deny",
    severity: "high",
    description: "Environment variable dump piped to network tool",
  },
];

export function analyzeCommand(
  command: string,
  segmentRules: readonly SegmentRule[],
  legacyRules: readonly DangerousPatternRule[],
): SegmentAnalysisResult {
  const segments = parseCommandSegments(command);

  const violations: SegmentAnalysisResult["violations"] = [];

  for (const rule of segmentRules) {
    if (rule.singleSegment) {
      for (const seg of segments) {
        if (rule.singleSegment.pattern.test(seg.raw.trim())) {
          violations.push({
            rule,
            leftSegment: seg,
            rightSegment: seg,
          });
        }
      }
      continue;
    }

    // Evaluate pairwise: any left segment piped (transitively) to any right segment
    for (let li = 0; li < segments.length; li++) {
      const left = segments[li];
      if (!left) continue;
      if (!rule.match.left.test(left.command)) continue;

      for (let ri = li + 1; ri < segments.length; ri++) {
        const right = segments[ri];
        if (!right) continue;
        // Check that there's at least one pipe connector between left and right
        let hasPipe = false;
        for (let ci = li; ci < ri; ci++) {
          if (segments[ci]?.connector === "|") {
            hasPipe = true;
            break;
          }
        }
        if (!hasPipe) continue;

        if (rule.match.right.test(right.command)) {
          violations.push({ rule, leftSegment: left, rightSegment: right });
        }
      }
    }
  }

  // Legacy flat matching
  const legacyMatches: SegmentAnalysisResult["legacyMatches"] = [];
  for (const rule of legacyRules) {
    if (rule.scope !== "bash") continue;
    const kind = rule.kind ?? "substring";
    if (kind === "regex") {
      try {
        const re = new RegExp(rule.match);
        if (re.test(command)) {
          legacyMatches.push({ rule, target: command });
        }
      } catch {
        // skip invalid regex
      }
    } else {
      if (command.includes(rule.match)) {
        legacyMatches.push({ rule, target: command });
      }
    }
  }

  return { segments, violations, legacyMatches };
}
