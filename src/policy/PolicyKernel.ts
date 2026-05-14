import fs from "node:fs/promises";
import path from "node:path";
import { parse as parseYaml, parseDocument } from "yaml";
import type { Workspace, WorkspaceIdentity } from "../storage/Workspace.js";
import type {
  HarnessRule,
  HarnessRuleAction,
  HarnessRuleCondition,
  HarnessRuleEnforcement,
  HarnessRuleTrigger,
  RuntimePolicy,
  RuntimePolicySnapshot,
  RuntimePolicyStatus,
  ResponseLanguagePolicy,
  BuiltinPresetId,
  BuiltinPresetConfig,
  BuiltinPresetMode,
} from "./policyTypes.js";
import type { ExternalHookConfig } from "../hooks/ExternalHookLoader.js";
import type { ClassifierDimensionDef } from "../hooks/builtin/classifierExtensions.js";

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

const AGENT_CONFIG_REL = "agent.config.yaml";
const DIRECTIVE_LINE_PREFIX_RE = /^\s*(?:[-*+]\s+|\d+[.)]\s+)?/;
const MARKDOWN_HEADING_RE = /^\s*#+\s+/;
const MAX_REGEX_PATTERN_CHARS = 300;
const MAX_INPUT_PATH_CHARS = 120;

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

function isLanguageDirective(text: string): ResponseLanguagePolicy | null {
  if (
    /(?:same\s+language|match(?:\s+the)?\s+user(?:'s)?\s+language|auto.?detect|자동\s*감지|사용자(?:가)?\s*(?:쓴|사용한)?\s*언어|같은\s*언어)/i.test(
      text,
    )
  ) {
    return "auto";
  }
  if (
    /(?:always|reply|answer|respond).*(?:korean)|(?:항상|한국어).*(?:답|응답)|한국어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "ko";
  }
  if (
    /(?:always|reply|answer|respond).*(?:english)|(?:항상|영어).*(?:답|응답)|영어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "en";
  }
  if (
    /(?:always|reply|answer|respond).*(?:japanese)|(?:항상|일본어).*(?:답|응답)|일본어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "ja";
  }
  if (
    /(?:always|reply|answer|respond).*(?:chinese)|(?:항상|중국어).*(?:답|응답)|중국어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "zh";
  }
  if (
    /(?:always|reply|answer|respond).*(?:spanish)|(?:항상|스페인어).*(?:답|응답)|스페인어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "es";
  }
  return null;
}

function generatedIdentityLanguageDirective(
  identityText: string | undefined,
): ResponseLanguagePolicy | null {
  if (!identityText) return null;
  const languageHeading = identityText.match(
    /(?:^|\n)##\s+Language\b([\s\S]*?)(?:\n##\s+|\s*$)/i,
  );
  const languageBlock = languageHeading?.[1]?.trim();
  if (!languageBlock) return null;
  if (/regardless\s+of\s+what\s+language\s+the\s+user\s+writes\s+in/i.test(languageBlock)) {
    return "auto";
  }
  return isLanguageDirective(languageBlock);
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
  const action = (() => {
    if (rule.action.type === "require_tool") {
      return `require_tool ${rule.action.toolName}`;
    }
    if (rule.action.type === "require_tool_input_match") {
      return `require_tool_input_match ${rule.action.toolName} ${rule.action.inputPath}`;
    }
    return rule.action.type;
  })();
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

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0
    ? value.trim()
    : null;
}

function validateRegexPattern(pattern: string, label: string): string | null {
  if (pattern.length > MAX_REGEX_PATTERN_CHARS) {
    return `${label} regex is too long`;
  }
  if (/\\[1-9]/.test(pattern)) {
    return `${label} regex uses unsupported backreferences`;
  }
  if (/\(\?<?[=!]/.test(pattern)) {
    return `${label} regex uses unsupported lookaround`;
  }
  if (/\([^)]*(?:[+*]|\{\d*,?\d*\})[^)]*\)\s*(?:[+*]|\{\d*,?\d*\})/.test(pattern)) {
    return `${label} regex is too complex`;
  }
  try {
    new RegExp(pattern, "iu");
  } catch {
    return `${label} regex is invalid`;
  }
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function booleanOrDefault(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function parseHarnessCondition(
  raw: unknown,
): { condition: HarnessRuleCondition; warning?: string } {
  if (!isRecord(raw)) return { condition: {} };
  const condition: HarnessRuleCondition = {};

  const userMessageMatches = nonEmptyString(
    raw.user_message_matches ?? raw.userMessageMatches,
  );
  if (userMessageMatches) {
    const warning = validateRegexPattern(
      userMessageMatches,
      "user_message_matches",
    );
    if (warning) return { condition, warning };
    condition.userMessageMatches = userMessageMatches;
  }

  const userMessageIncludes = raw.user_message_includes ?? raw.userMessageIncludes;
  if (Array.isArray(userMessageIncludes)) {
    const includes = userMessageIncludes
      .map((item) => nonEmptyString(item))
      .filter((item): item is string => item !== null)
      .slice(0, 20);
    if (includes.length > 0) condition.userMessageIncludes = includes;
  }

  const toolName = nonEmptyString(raw.tool_name ?? raw.toolName);
  if (toolName) condition.toolName = toolName;

  const anyToolUsed = raw.any_tool_used ?? raw.anyToolUsed;
  if (Array.isArray(anyToolUsed)) {
    const tools = anyToolUsed
      .map((item) => nonEmptyString(item))
      .filter((item): item is string => item !== null)
      .slice(0, 50);
    if (tools.length > 0) condition.anyToolUsed = tools;
  }

  return { condition };
}

function parseHarnessAction(raw: unknown): {
  action?: HarnessRuleAction;
  warning?: string;
} {
  if (!isRecord(raw)) {
    return { warning: "require/action block is missing" };
  }

  const directType = nonEmptyString(raw.type);
  if (directType === "require_tool") {
    const toolName = nonEmptyString(raw.toolName ?? raw.tool_name ?? raw.tool);
    if (!toolName) return { warning: "require_tool action is missing toolName" };
    return { action: { type: "require_tool", toolName } };
  }

  if (directType === "llm_verifier") {
    const prompt = nonEmptyString(raw.prompt);
    if (!prompt) return { warning: "llm_verifier action is missing prompt" };
    return { action: makeVerifierAction(prompt) };
  }

  if (directType === "block") {
    const reason = nonEmptyString(raw.reason);
    if (!reason) return { warning: "block action is missing reason" };
    return { action: { type: "block", reason } };
  }

  const toolName = nonEmptyString(raw.tool ?? raw.toolName ?? raw.tool_name);
  const commandPattern = nonEmptyString(
    raw.input_command_matches ?? raw.inputCommandMatches,
  );
  const genericPattern = nonEmptyString(raw.pattern ?? raw.input_matches);
  const inputPath =
    nonEmptyString(raw.input_path ?? raw.inputPath) ??
    (commandPattern ? "command" : null);
  const pattern = commandPattern ?? genericPattern;

  if (toolName && inputPath && pattern) {
    if (inputPath.length > MAX_INPUT_PATH_CHARS) {
      return { warning: "input path is too long" };
    }
    const warning = validateRegexPattern(
      pattern,
      commandPattern ? "input_command_matches" : "pattern",
    );
    if (warning) return { warning };
    return {
      action: {
        type: "require_tool_input_match",
        toolName,
        inputPath,
        pattern,
      },
    };
  }

  return { warning: "unsupported harness action" };
}

function parseStructuredHarnessRule(
  raw: unknown,
  index: number,
): { rule?: HarnessRule; warning?: string } {
  if (!isRecord(raw)) {
    return { warning: `ignored harness rule ${index + 1}: rule must be an object` };
  }

  const id = nonEmptyString(raw.id) ?? `config-harness-rule-${index + 1}`;
  const trigger = nonEmptyString(raw.trigger) ?? "beforeCommit";
  if (trigger !== "beforeCommit" && trigger !== "afterToolUse") {
    return { warning: `ignored harness rule ${id}: unsupported trigger ${trigger}` };
  }

  const enforcement = nonEmptyString(raw.enforcement) ?? "block_on_fail";
  if (enforcement !== "audit" && enforcement !== "block_on_fail") {
    return {
      warning: `ignored harness rule ${id}: unsupported enforcement ${enforcement}`,
    };
  }

  const conditionResult = parseHarnessCondition(raw.when ?? raw.condition);
  if (conditionResult.warning) {
    return { warning: `ignored harness rule ${id}: ${conditionResult.warning}` };
  }

  const actionResult = parseHarnessAction(raw.require ?? raw.action);
  if (actionResult.warning || !actionResult.action) {
    return {
      warning: `ignored harness rule ${id}: ${actionResult.warning ?? "invalid action"}`,
    };
  }

  const timeoutMs =
    typeof raw.timeoutMs === "number" && Number.isFinite(raw.timeoutMs)
      ? Math.min(10_000, Math.max(500, Math.trunc(raw.timeoutMs)))
      : 2_000;

  return {
    rule: {
      id,
      sourceText: `agent.config.yaml:harness_rules.${id}`,
      enabled: booleanOrDefault(raw.enabled, true),
      trigger,
      condition:
        Object.keys(conditionResult.condition).length > 0
          ? conditionResult.condition
          : undefined,
      action: actionResult.action,
      enforcement,
      timeoutMs,
    },
  };
}

async function loadStructuredHarnessRules(
  workspace: Workspace,
): Promise<{ rules: HarnessRule[]; warnings: string[] }> {
  let raw: string;
  try {
    raw = await fs.readFile(path.join(workspace.root, AGENT_CONFIG_REL), "utf8");
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      return { rules: [], warnings: [] };
    }
    return {
      rules: [],
      warnings: [`failed to read ${AGENT_CONFIG_REL}: ${(err as Error).message}`],
    };
  }

  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch (err) {
    return {
      rules: [],
      warnings: [`failed to parse ${AGENT_CONFIG_REL}: ${(err as Error).message}`],
    };
  }
  if (!isRecord(parsed)) return { rules: [], warnings: [] };

  const rawRules = parsed.harness_rules ?? parsed.harnessRules;
  if (!Array.isArray(rawRules)) return { rules: [], warnings: [] };

  const rules: HarnessRule[] = [];
  const warnings: string[] = [];
  for (const [index, rawRule] of rawRules.entries()) {
    const result = parseStructuredHarnessRule(rawRule, index);
    if (result.warning) warnings.push(result.warning);
    if (result.rule) rules.push(result.rule);
  }
  return { rules, warnings };
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
  if (policy.harnessRules.some((existing) => existing.id === rule.id)) return;
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

function parseCondition(
  value: unknown,
  warnings: string[],
  sourcePath: string,
): HarnessRuleCondition | undefined {
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
  const userMessageMatches = nonEmptyString(
    data.userMessageMatches ?? data.user_message_matches,
  );
  if (userMessageMatches) {
    const warning = validateRegexPattern(userMessageMatches, "userMessageMatches");
    if (warning) {
      warnings.push(`harness rule ${sourcePath} ignored userMessageMatches: ${warning}`);
    } else {
      condition.userMessageMatches = userMessageMatches;
    }
  }
  return Object.keys(condition).length > 0 ? condition : undefined;
}

function parseAction(
  value: unknown,
  body: string,
  warnings: string[],
  sourcePath: string,
): HarnessRuleAction | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  if (data.type === "require_tool" && typeof data.toolName === "string") {
    return { type: "require_tool", toolName: data.toolName };
  }
  if (data.type === "require_tool_input_match") {
    const toolName = nonEmptyString(data.toolName ?? data.tool_name ?? data.tool);
    const commandPattern = nonEmptyString(
      data.inputCommandMatches ?? data.input_command_matches,
    );
    const inputPath = nonEmptyString(data.inputPath ?? data.input_path) ??
      (commandPattern ? "command" : null);
    const pattern = nonEmptyString(data.pattern ?? data.inputMatches ?? data.input_matches) ??
      commandPattern;
    if (!toolName || !inputPath || !pattern) return null;
    if (inputPath.length > MAX_INPUT_PATH_CHARS) {
      warnings.push(`harness rule ${sourcePath} ignored: inputPath is too long`);
      return null;
    }
    const warning = validateRegexPattern(pattern, "input match");
    if (warning) {
      warnings.push(`harness rule ${sourcePath} ignored: ${warning}`);
      return null;
    }
    return {
      type: "require_tool_input_match",
      toolName,
      inputPath,
      pattern,
    };
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
  const action = parseAction(data.action, body, warnings, sourcePath);
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
    condition: parseCondition(data.condition, warnings, sourcePath),
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
  const identityLanguage = generatedIdentityLanguageDirective(identity.identity);
  if (identityLanguage) {
    policy.responseMode.language = identityLanguage;
  }

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

export interface AgentConfigExtensions {
  disableBuiltinHooks: string[];
  customHooks?: ExternalHookConfig;
}

export async function loadAgentConfigExtensions(
  workspace: Workspace,
): Promise<{ extensions: AgentConfigExtensions; warnings: string[] }> {
  const warnings: string[] = [];
  const extensions: AgentConfigExtensions = { disableBuiltinHooks: [] };

  let raw: string;
  try {
    raw = await fs.readFile(
      path.join(workspace.root, AGENT_CONFIG_REL),
      "utf8",
    );
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      return { extensions, warnings };
    }
    return {
      extensions,
      warnings: [
        `failed to read ${AGENT_CONFIG_REL}: ${(err as Error).message}`,
      ],
    };
  }

  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch (err) {
    return {
      extensions,
      warnings: [
        `failed to parse ${AGENT_CONFIG_REL}: ${(err as Error).message}`,
      ],
    };
  }
  if (!isRecord(parsed)) return { extensions, warnings };

  // disable_builtin_hooks
  const disableList =
    parsed.disable_builtin_hooks ?? parsed.disableBuiltinHooks;
  if (Array.isArray(disableList)) {
    extensions.disableBuiltinHooks = disableList
      .filter(
        (item): item is string =>
          typeof item === "string" && item.trim().length > 0,
      )
      .map((item) => item.trim());
  }

  // custom_hooks
  const customHooks = parsed.custom_hooks ?? parsed.customHooks;
  if (isRecord(customHooks)) {
    const dir = nonEmptyString(customHooks.directory) ?? "./hooks";
    const autoDiscover =
      typeof customHooks.auto_discover === "boolean"
        ? customHooks.auto_discover
        : true;
    const hooks: Array<{
      file: string;
      enabled?: boolean;
      priority?: number;
      config?: Record<string, unknown>;
    }> = [];

    const hooksList = customHooks.hooks;
    if (Array.isArray(hooksList)) {
      for (const hookEntry of hooksList) {
        if (!isRecord(hookEntry)) continue;
        const file = nonEmptyString(hookEntry.file);
        if (!file) {
          warnings.push("custom_hooks entry missing file field");
          continue;
        }
        hooks.push({
          file,
          enabled:
            typeof hookEntry.enabled === "boolean"
              ? hookEntry.enabled
              : undefined,
          priority:
            typeof hookEntry.priority === "number"
              ? hookEntry.priority
              : undefined,
          config: isRecord(hookEntry.config)
            ? (hookEntry.config as Record<string, unknown>)
            : undefined,
        });
      }
    }

    extensions.customHooks = {
      directory: dir,
      autoDiscover,
      hooks: hooks.length > 0 ? hooks : undefined,
    };
  }

  return { extensions, warnings };
}

export async function loadClassifierDimensions(
  workspace: Workspace,
): Promise<{ dimensions: ClassifierDimensionDef[]; warnings: string[] }> {
  const warnings: string[] = [];
  const dimensions: ClassifierDimensionDef[] = [];

  let raw: string;
  try {
    raw = await fs.readFile(path.join(workspace.root, AGENT_CONFIG_REL), "utf8");
  } catch {
    return { dimensions, warnings };
  }

  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch {
    return { dimensions, warnings };
  }
  if (!isRecord(parsed)) return { dimensions, warnings };

  const rawDims = parsed.classifier_dimensions ?? parsed.classifierDimensions;
  if (!isRecord(rawDims)) return { dimensions, warnings };

  for (const phase of ["request", "finalAnswer"] as const) {
    const phaseKey = phase === "finalAnswer" ? "final_answer" : phase;
    const rawList =
      (rawDims as Record<string, unknown>)[phaseKey] ??
      (rawDims as Record<string, unknown>)[phase];
    if (!Array.isArray(rawList)) continue;

    for (const entry of rawList) {
      if (!isRecord(entry)) continue;
      const name = nonEmptyString(entry.name);
      if (!name) {
        warnings.push("classifier dimension missing name");
        continue;
      }
      const schema = entry.schema;
      if (!isRecord(schema)) {
        warnings.push(`classifier dimension ${name} missing schema`);
        continue;
      }
      const instructions = nonEmptyString(entry.instructions);
      if (!instructions) {
        warnings.push(`classifier dimension ${name} missing instructions`);
        continue;
      }

      const schemaMap: Record<string, string> = {};
      for (const [k, v] of Object.entries(schema)) {
        schemaMap[k] = typeof v === "string" ? v : String(v);
      }

      dimensions.push({ name, phase, schema: schemaMap, instructions });
    }
  }

  return { dimensions, warnings };
}

// ── Builtin Preset Definitions ──────────────────────────────────

interface PresetDef {
  id: BuiltinPresetId;
  hookId: string;
  trigger: "beforeCommit";
  priority: number;
  defaultEnabled: boolean;
  defaultMode: BuiltinPresetMode;
  envEnabledKey?: string;
  envModeKey?: string;
  timeoutMs: number;
}

const BUILTIN_PRESET_DEFS: PresetDef[] = [
  {
    id: "self-claim", hookId: "builtin-preset:self-claim",
    trigger: "beforeCommit", priority: 80, defaultEnabled: true, defaultMode: "hybrid",
    envEnabledKey: "MAGI_DETERMINISTIC_SELF_CLAIM", envModeKey: "MAGI_HYBRID_SELF_CLAIM",
    timeoutMs: 5_000,
  },
  {
    id: "fact-grounding", hookId: "builtin-preset:fact-grounding",
    trigger: "beforeCommit", priority: 82, defaultEnabled: false, defaultMode: "hybrid",
    envEnabledKey: "MAGI_FACT_GROUNDING", envModeKey: "MAGI_HYBRID_GROUNDING",
    timeoutMs: 15_000,
  },
  {
    id: "response-language", hookId: "builtin-preset:response-language",
    trigger: "beforeCommit", priority: 85, defaultEnabled: true, defaultMode: "hybrid",
    envEnabledKey: "MAGI_RESPONSE_LANGUAGE_GATE", envModeKey: "MAGI_HYBRID_LANG",
    timeoutMs: 9_000,
  },
  {
    id: "deterministic-evidence", hookId: "builtin-preset:deterministic-evidence",
    trigger: "beforeCommit", priority: 88, defaultEnabled: true, defaultMode: "hybrid",
    envEnabledKey: "MAGI_DETERMINISTIC_EVIDENCE_VERIFY", envModeKey: "MAGI_HYBRID_EVIDENCE",
    timeoutMs: 11_000,
  },
  {
    id: "answer-quality", hookId: "builtin-preset:answer-quality",
    trigger: "beforeCommit", priority: 90, defaultEnabled: true, defaultMode: "hybrid",
    envEnabledKey: "MAGI_ANSWER_VERIFY", envModeKey: "MAGI_HYBRID_ANSWER",
    timeoutMs: 16_000,
  },
];

function resolvePresetEnabled(def: PresetDef, yamlEnabled?: boolean): boolean {
  if (def.envEnabledKey) {
    const envVal = process.env[def.envEnabledKey]?.trim().toLowerCase();
    if (envVal === "off" || envVal === "false" || envVal === "0") return false;
    if (envVal === "on" || envVal === "true" || envVal === "1") return true;
  }
  if (yamlEnabled !== undefined) return yamlEnabled;
  return def.defaultEnabled;
}

function resolvePresetMode(def: PresetDef, yamlMode?: string): BuiltinPresetMode {
  if (def.envModeKey && process.env[def.envModeKey] === "1") return "hybrid";
  if (yamlMode === "hybrid" || yamlMode === "deterministic" || yamlMode === "llm") {
    return yamlMode;
  }
  return def.defaultMode;
}

export function loadBuiltinPresets(
  yamlPresets?: Record<string, unknown>,
): HarnessRule[] {
  return BUILTIN_PRESET_DEFS.map((def) => {
    const yamlConfig = isRecord(yamlPresets?.[def.id]) ? yamlPresets[def.id] as Record<string, unknown> : undefined;
    const enabled = resolvePresetEnabled(
      def,
      yamlConfig ? booleanOrDefault(yamlConfig.enabled, def.defaultEnabled) : undefined,
    );
    const mode = resolvePresetMode(
      def,
      yamlConfig ? nonEmptyString(yamlConfig.mode) ?? undefined : undefined,
    );
    return {
      id: def.hookId,
      sourceText: `builtin_preset:${def.id}`,
      enabled,
      trigger: def.trigger,
      action: {
        type: "builtin_preset" as const,
        preset: def.id,
        config: { enabled, mode },
      },
      enforcement: "block_on_fail" as const,
      timeoutMs: def.timeoutMs,
      priority: def.priority,
    };
  });
}

function loadBuiltinPresetsFromYaml(
  _workspace: Workspace,
  parsedYaml?: unknown,
): HarnessRule[] {
  if (!isRecord(parsedYaml)) return loadBuiltinPresets();
  const rawPresets = parsedYaml.builtin_presets ?? parsedYaml.builtinPresets;
  if (!isRecord(rawPresets)) return loadBuiltinPresets();
  return loadBuiltinPresets(rawPresets as Record<string, unknown>);
}

export class PolicyKernel {
  constructor(private readonly workspace: Workspace) {}

  async current(): Promise<RuntimePolicySnapshot> {
    const identity = await this.workspace.loadIdentity();
    const snapshot = parseUserRules(identity);
    const structured = await loadStructuredHarnessRules(this.workspace);

    // Load parsed yaml for builtin presets
    let parsedYaml: unknown;
    try {
      const raw = await fs.readFile(path.join(this.workspace.root, AGENT_CONFIG_REL), "utf8");
      parsedYaml = parseYaml(raw);
    } catch { /* no yaml = use defaults */ }

    // Inject builtin presets as HarnessRules
    const presets = loadBuiltinPresetsFromYaml(this.workspace, parsedYaml);
    for (const preset of presets) {
      addHarnessRule(snapshot.policy, preset);
    }

    for (const rule of structured.rules) {
      addHarnessRule(snapshot.policy, rule);
    }
    snapshot.status.warnings.push(...structured.warnings);
    snapshot.status.harnessDirectives =
      snapshot.policy.harnessRules.map(harnessDirective);
    return snapshot;
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
