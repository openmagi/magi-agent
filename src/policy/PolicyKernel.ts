import type { Workspace, WorkspaceIdentity } from "../storage/Workspace.js";
import { parseDocument } from "yaml";
import type {
  HarnessRule,
  HarnessRuleAction,
  HarnessRuleCondition,
  HarnessRuleEnforcement,
  HarnessRuleTrigger,
  RuntimePolicy,
  RuntimePolicySnapshot,
  RuntimePolicyStatus,
  SupportedLanguage,
} from "./policyTypes.js";

const DEFAULT_POLICY: RuntimePolicy = {
  approval: {
    explicitConsentForExternalActions: true,
  },
  verification: {
    requireCompletionEvidence: true,
    honorTaskContractVerificationMode: true,
  },
  delivery: {
    requireDeliveredArtifactsBeforeCompletion: true,
  },
  async: {
    requireRealNotificationMechanism: true,
  },
  retry: {
    retryTransientToolFailures: true,
    defaultBackoffSeconds: [0, 10, 30],
  },
  responseMode: {},
  citations: {},
  harnessRules: [],
};

const DIRECTIVE_LINE_PREFIX_RE = /^\s*(?:[-*+]\s+|\d+[.)]\s+)?/;
const MARKDOWN_HEADING_RE = /^\s*#+\s+/;

function cloneDefaultPolicy(): RuntimePolicy {
  return {
    approval: { ...DEFAULT_POLICY.approval },
    verification: { ...DEFAULT_POLICY.verification },
    delivery: { ...DEFAULT_POLICY.delivery },
    async: { ...DEFAULT_POLICY.async },
    retry: {
      ...DEFAULT_POLICY.retry,
      defaultBackoffSeconds: [...DEFAULT_POLICY.retry.defaultBackoffSeconds],
    },
    responseMode: { ...DEFAULT_POLICY.responseMode },
    citations: { ...DEFAULT_POLICY.citations },
    harnessRules: [...DEFAULT_POLICY.harnessRules],
  };
}

function cleanRuleLine(line: string): string {
  return line
    .replace(MARKDOWN_HEADING_RE, "")
    .replace(DIRECTIVE_LINE_PREFIX_RE, "")
    .trim();
}

function isLanguageDirective(text: string): SupportedLanguage | null {
  if (
    /(?:always|reply|answer).*(?:korean)|(?:항상|한국어).*(?:답|응답)|한국어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "ko";
  }
  if (
    /(?:always|reply|answer).*(?:english)|(?:항상|영어).*(?:답|응답)|영어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "en";
  }
  if (
    /(?:always|reply|answer).*(?:japanese)|(?:항상|일본어).*(?:답|응답)|일본어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "ja";
  }
  if (
    /(?:always|reply|answer).*(?:chinese)|(?:항상|중국어).*(?:답|응답)|중국어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "zh";
  }
  if (
    /(?:always|reply|answer).*(?:spanish)|(?:항상|스페인어).*(?:답|응답)|스페인어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "es";
  }
  return null;
}

function normalizeUserRules(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split("\n")
    .map((line) => cleanRuleLine(line))
    .filter((line) => line.length > 0 && line !== "[truncated]");
}

function buildPlatformDirectives(policy: RuntimePolicy): string[] {
  return [
    `approval.explicit_consent_for_external_actions=${String(policy.approval.explicitConsentForExternalActions)}`,
    `verification.require_completion_evidence=${String(policy.verification.requireCompletionEvidence)}`,
    `verification.honor_task_contract_verification_mode=${String(policy.verification.honorTaskContractVerificationMode)}`,
    `delivery.require_delivered_artifacts_before_completion=${String(policy.delivery.requireDeliveredArtifactsBeforeCompletion)}`,
    `async.require_real_notification_mechanism=${String(policy.async.requireRealNotificationMechanism)}`,
    `retry.retry_transient_tool_failures=${String(policy.retry.retryTransientToolFailures)}`,
    `retry.default_backoff_seconds=${policy.retry.defaultBackoffSeconds.join(",")}`,
  ];
}

function buildUserDirectives(policy: RuntimePolicy): string[] {
  const directives: string[] = [];
  if (policy.responseMode.language) {
    directives.push(`response.language=${policy.responseMode.language}`);
  }
  if (policy.citations.requireSources) {
    directives.push("citations.require_sources=true");
  }
  if (policy.citations.includePageNumbers) {
    directives.push("citations.include_page_numbers=true");
  }
  if (policy.responseMode.noProfanity) {
    directives.push("response.no_profanity=true");
  }
  if (policy.responseMode.concise) {
    directives.push("response.concise=true");
  }
  return directives;
}

function harnessDirective(rule: HarnessRule): string {
  const action =
    rule.action.type === "require_tool"
      ? `require_tool ${rule.action.toolName}`
      : rule.action.type;
  return `${rule.id} ${rule.trigger} ${action} ${rule.enforcement}`;
}

function isGeneratedUserRulesBoilerplate(line: string): boolean {
  return (
    line === "User-Defined Agent Rules" ||
    /^These rules were set by the bot owner/i.test(line) ||
    /^Platform rules take priority/i.test(line)
  );
}

function makeVerifierAction(prompt: string): HarnessRuleAction {
  return {
    type: "llm_verifier",
    prompt: prompt.slice(0, 1200),
  };
}

function compileHarnessRule(line: string): HarnessRule | null {
  if (
    /(?=.*(?:파일|문서|리포트|보고서|artifact|file|document|report))(?=.*(?:만들|생성|작성|create|generate|write))(?=.*(?:첨부|채팅|전달|attach|attachment|deliver|send))/i.test(
      line,
    )
  ) {
    return {
      id: "user-harness:file-delivery-after-create",
      sourceText: line,
      enabled: true,
      trigger: "beforeCommit",
      condition: {
        anyToolUsed: [
          "DocumentWrite",
          "SpreadsheetWrite",
          "FileWrite",
          "FileEdit",
          "ArtifactCreate",
          "ArtifactUpdate",
        ],
      },
      action: { type: "require_tool", toolName: "FileDeliver" },
      enforcement: "block_on_fail",
      timeoutMs: 2_000,
    };
  }

  if (
    /(?:최종\s*답변|답변\s*전|final\s+answer|before\s+(?:the\s+)?answer|before\s+final).*(?:검사|확인|검증|verify|check|double.?check)|(?:한\s*번\s*더|다시).*(?:검사|확인|검증|verify|check)/i.test(
      line,
    )
  ) {
    return {
      id: "user-harness:final-answer-verifier",
      sourceText: line,
      enabled: true,
      trigger: "beforeCommit",
      action: makeVerifierAction(
        [
          "Check whether the assistant's final answer satisfies the user's request and does not skip requested deliverables.",
          "Reply with exactly `PASS` or `FAIL: <short reason>`.",
        ].join("\n"),
      ),
      enforcement: "block_on_fail",
      timeoutMs: 8_000,
    };
  }

  if (
    /(?:출처|근거|citation|citations|source|sources).*(?:확인|검사|검증|명시|포함|check|verify|include|cite)|(?:확인|검사|검증|check|verify).*(?:출처|근거|citation|source)/i.test(
      line,
    )
  ) {
    return {
      id: "user-harness:source-grounding-verifier",
      sourceText: line,
      enabled: true,
      trigger: "beforeCommit",
      action: makeVerifierAction(
        [
          "Check whether factual claims that need support are grounded in cited or explicitly named sources.",
          "If the answer makes unsupported factual claims, reply `FAIL: missing source grounding`.",
          "If the answer is casual, self-contained, or explicitly says verification was not possible, reply `PASS`.",
        ].join("\n"),
      ),
      enforcement: "block_on_fail",
      timeoutMs: 8_000,
    };
  }

  return null;
}

function addHarnessRule(policy: RuntimePolicy, rule: HarnessRule): void {
  const index = policy.harnessRules.findIndex((existing) => existing.id === rule.id);
  if (index >= 0) {
    policy.harnessRules[index] = rule;
    return;
  }
  policy.harnessRules.push(rule);
}

function parseFrontmatterMarkdown(
  content: string,
): { data: Record<string, unknown>; body: string } | null {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/);
  if (!match) return null;
  const doc = parseDocument(match[1] ?? "");
  if (doc.errors.length > 0) return null;
  const value = doc.toJSON();
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return { data: value as Record<string, unknown>, body: match[2] ?? "" };
}

function stringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out = value.filter((item): item is string => typeof item === "string");
  return out.length > 0 ? out : undefined;
}

function parseCondition(value: unknown): HarnessRuleCondition | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const data = value as Record<string, unknown>;
  const condition: HarnessRuleCondition = {};
  if (typeof data.toolName === "string" && data.toolName.trim()) {
    condition.toolName = data.toolName.trim();
  }
  const anyToolUsed = stringArray(data.anyToolUsed);
  if (anyToolUsed) condition.anyToolUsed = anyToolUsed;
  const userMessageIncludes = stringArray(data.userMessageIncludes);
  if (userMessageIncludes) condition.userMessageIncludes = userMessageIncludes;
  return Object.keys(condition).length > 0 ? condition : undefined;
}

function parseAction(
  value: unknown,
  body: string,
): HarnessRuleAction | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  if (data.type === "require_tool" && typeof data.toolName === "string") {
    return { type: "require_tool", toolName: data.toolName };
  }
  if (data.type === "llm_verifier") {
    const prompt =
      typeof data.prompt === "string" && data.prompt.trim().length > 0
        ? data.prompt
        : body.trim();
    if (!prompt) return null;
    return makeVerifierAction(prompt);
  }
  if (data.type === "block") {
    const reason =
      typeof data.reason === "string" && data.reason.trim().length > 0
        ? data.reason
        : body.trim();
    if (!reason) return null;
    return { type: "block", reason: reason.slice(0, 1200) };
  }
  return null;
}

function isTrigger(value: unknown): value is HarnessRuleTrigger {
  return value === "beforeCommit" || value === "afterToolUse";
}

function isEnforcement(value: unknown): value is HarnessRuleEnforcement {
  return value === "audit" || value === "block_on_fail";
}

function firstBodyLine(body: string): string {
  return (
    body
      .split(/\r?\n/)
      .map((line) => cleanRuleLine(line))
      .find((line) => line.length > 0 && line !== "[truncated]") ?? ""
  );
}

function compileStructuredHarnessRule(
  sourcePath: string,
  content: string,
  warnings: string[],
): HarnessRule | null {
  const parsed = parseFrontmatterMarkdown(content);
  if (!parsed) return null;
  const { data, body } = parsed;
  const id = typeof data.id === "string" ? data.id.trim() : "";
  if (!id) {
    warnings.push(`harness rule ${sourcePath} ignored: missing id`);
    return null;
  }
  if (!isTrigger(data.trigger)) {
    warnings.push(`harness rule ${id} ignored: invalid trigger`);
    return null;
  }
  const action = parseAction(data.action, body);
  if (!action) {
    warnings.push(`harness rule ${id} ignored: invalid action`);
    return null;
  }
  const enforcement = isEnforcement(data.enforcement)
    ? data.enforcement
    : "block_on_fail";
  const timeoutMs =
    typeof data.timeoutMs === "number" && Number.isFinite(data.timeoutMs)
      ? Math.max(500, Math.min(30_000, Math.floor(data.timeoutMs)))
      : 8_000;
  const sourceText =
    typeof data.sourceText === "string" && data.sourceText.trim().length > 0
      ? data.sourceText.trim()
      : typeof data.description === "string" && data.description.trim().length > 0
        ? data.description.trim()
        : firstBodyLine(body) || id;

  return {
    id,
    sourceText,
    enabled: typeof data.enabled === "boolean" ? data.enabled : true,
    trigger: data.trigger,
    condition: parseCondition(data.condition),
    action,
    enforcement,
    timeoutMs,
  };
}

function parseUserRules(identity: WorkspaceIdentity): RuntimePolicySnapshot {
  const policy = cloneDefaultPolicy();
  const warnings: string[] = [];
  const advisoryDirectives: string[] = [];
  const lines = normalizeUserRules(identity.userRules);

  for (const file of identity.userHarnessRules ?? []) {
    const structured = compileStructuredHarnessRule(file.path, file.content, warnings);
    if (structured) {
      addHarnessRule(policy, structured);
      continue;
    }
    for (const line of normalizeUserRules(file.content)) {
      const rule = compileHarnessRule(line);
      if (rule) addHarnessRule(policy, rule);
    }
  }

  for (const line of lines) {
    if (isGeneratedUserRulesBoilerplate(line)) continue;

    const harnessRule = compileHarnessRule(line);
    if (harnessRule) {
      addHarnessRule(policy, harnessRule);
      continue;
    }

    const language = isLanguageDirective(line);
    if (language) {
      if (policy.responseMode.language && policy.responseMode.language !== language) {
        warnings.push(
          `conflicting response.language directives detected; keeping response.language=${language}`,
        );
      }
      policy.responseMode.language = language;
      continue;
    }

    if (
      /(?:page\s*number|페이지\s*번호).*(?:cit|인용|출처)|(?:cit|인용|출처).*(?:page\s*number|페이지\s*번호)/i.test(
        line,
      )
    ) {
      policy.citations.requireSources = true;
      policy.citations.includePageNumbers = true;
      continue;
    }

    if (/(?:cit|source|출처|인용)/i.test(line)) {
      policy.citations.requireSources = true;
      continue;
    }

    if (/(?:no\s*profanity|don't\s*swear|do not swear|비속어\s*금지|욕설\s*금지)/i.test(line)) {
      policy.responseMode.noProfanity = true;
      continue;
    }

    if (/(?:be\s*concise|brief|concise|간결|짧게)/i.test(line)) {
      policy.responseMode.concise = true;
      continue;
    }

    advisoryDirectives.push(line);
  }

  return {
    policy,
    status: {
      executableDirectives: buildPlatformDirectives(policy),
      userDirectives: buildUserDirectives(policy),
      harnessDirectives: policy.harnessRules.map(harnessDirective),
      advisoryDirectives,
      warnings,
    },
  };
}

export class PolicyKernel {
  constructor(private readonly workspace: Workspace) {}

  async current(): Promise<RuntimePolicySnapshot> {
    const identity = await this.workspace.loadIdentity();
    return parseUserRules(identity);
  }

  async status(): Promise<RuntimePolicyStatus> {
    return (await this.current()).status;
  }
}

export function buildRuntimePolicyBlock(snapshot: RuntimePolicySnapshot): string {
  const lines: string[] = [];
  lines.push(`<runtime_policy source="policy-kernel">`);

  if (snapshot.status.executableDirectives.length > 0) {
    lines.push("[platform]");
    lines.push(...snapshot.status.executableDirectives);
  }
  if (snapshot.status.userDirectives.length > 0) {
    lines.push("[user]");
    lines.push(...snapshot.status.userDirectives);
  }
  if (snapshot.status.harnessDirectives.length > 0) {
    lines.push("[harness]");
    lines.push(...snapshot.status.harnessDirectives);
  }
  if (snapshot.status.advisoryDirectives.length > 0) {
    lines.push("[advisory]");
    lines.push(...snapshot.status.advisoryDirectives);
  }
  if (snapshot.status.warnings.length > 0) {
    lines.push("[warnings]");
    lines.push(...snapshot.status.warnings);
  }

  lines.push("</runtime_policy>");
  return lines.join("\n");
}
