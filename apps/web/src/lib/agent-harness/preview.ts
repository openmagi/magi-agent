export type AgentRulesPreviewControlKind = "harness" | "policy";

export interface AgentRulesPreviewControl {
  id: string;
  kind: AgentRulesPreviewControlKind;
  title: string;
  summary: string;
  trigger: string;
  action: string;
  enforcement: string;
  sourceText: string;
}

export interface AgentRulesPreview {
  controls: AgentRulesPreviewControl[];
  warnings: string[];
  advisoryRules: string[];
}

const CONTROL_PATTERNS: Array<{
  id: string;
  kind: AgentRulesPreviewControlKind;
  title: string;
  summary: string;
  trigger: string;
  action: string;
  enforcement: string;
  pattern: RegExp;
}> = [
  {
    id: "user-harness:file-delivery-after-create",
    kind: "harness",
    title: "File attachment check",
    summary: "Requires created files to be delivered before completion.",
    trigger: "afterFileCreate",
    action: "requireFileDelivery",
    enforcement: "blockAndRetry",
    pattern: /\b(file|deliver|attach|attachment|created file)\b/i,
  },
  {
    id: "user-harness:final-answer-verifier",
    kind: "harness",
    title: "Final answer check",
    summary: "Verifies that the final answer satisfies requested deliverables.",
    trigger: "beforeCommit",
    action: "verifyDeliverables",
    enforcement: "blockAndRetry",
    pattern: /\b(final answer|deliverable|complete|completion|verify)\b/i,
  },
  {
    id: "user-harness:source-grounding-verifier",
    kind: "harness",
    title: "Source grounding check",
    summary: "Verifies claims that need source support.",
    trigger: "beforeCommit",
    action: "verifySources",
    enforcement: "blockAndRetry",
    pattern: /\b(source|citation|cite|ground|evidence)\b/i,
  },
  {
    id: "user-harness:external-action-confirmation",
    kind: "harness",
    title: "External action confirmation",
    summary: "Asks before taking actions outside the local workspace.",
    trigger: "beforeExternalAction",
    action: "askConfirmation",
    enforcement: "askUser",
    pattern: /\b(email|payment|post|publish|upload|external)\b/i,
  },
  {
    id: "policy:progress-updates",
    kind: "policy",
    title: "Progress updates",
    summary: "Requests visible progress updates during long-running work.",
    trigger: "duringLongTask",
    action: "sendProgressUpdate",
    enforcement: "recordOnly",
    pattern: /\b(progress|update|long task|silent)\b/i,
  },
];

function ruleLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.replace(/^\s*(?:[-*+]\s+|\d+[.)]\s+)?/, "").trim())
    .filter(Boolean);
}

export function compileAgentRulesPreview(value: string): AgentRulesPreview {
  const lines = ruleLines(value);
  const controls: AgentRulesPreviewControl[] = [];
  const advisoryRules: string[] = [];
  const seen = new Set<string>();

  for (const line of lines) {
    const control = CONTROL_PATTERNS.find((candidate) => candidate.pattern.test(line));
    if (!control) {
      advisoryRules.push(line);
      continue;
    }
    if (seen.has(control.id)) continue;
    seen.add(control.id);
    controls.push({ ...control, sourceText: line });
  }

  return {
    controls,
    warnings: value.length > 5000 ? ["Agent rules exceed the local preview limit."] : [],
    advisoryRules,
  };
}
