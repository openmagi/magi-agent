export type AdminHarnessTrigger = "beforeCommit" | "afterToolUse";
export type AdminHarnessEnforcement = "block_on_fail" | "audit";

export interface AdminHarnessRuleDraft {
  id: string;
  enabled: boolean;
  description?: string;
  trigger: AdminHarnessTrigger;
  userMessageMatches: string;
  toolName: string;
  inputPath: string;
  inputPattern: string;
  enforcement: AdminHarnessEnforcement;
}

export interface HarnessRuleValidationError {
  index: number;
  field: keyof AdminHarnessRuleDraft | "rules";
  message: string;
}

export const TOSS_POS_HARNESS_RULE: AdminHarnessRuleDraft = {
  id: "tossplace-merchant-grounding",
  enabled: true,
  description: "Require live Toss POS merchant lookup before answering connection-state questions.",
  trigger: "beforeCommit",
  userMessageMatches: "(토스|토스플레이스|POS).*(연결|연동|해제|등록|매장)",
  toolName: "Bash",
  inputPath: "command",
  inputPattern: "integration\\.sh\\s+['\"]?tossplace/my-merchants",
  enforcement: "block_on_fail",
};

const MAX_RULES = 20;
const MAX_AGENT_CONFIG_CHARS = 64 * 1024;
const MAX_REGEX_PATTERN_CHARS = 300;
const MAX_INPUT_PATH_CHARS = 120;
const HARNESS_HEADER_RE = /^harness_rules\s*:\s*(?:\[\])?\s*(?:#.*)?$/;
const TOP_LEVEL_KEY_RE = /^[A-Za-z0-9_-][A-Za-z0-9_-]*\s*:/;

function yamlQuote(value: string): string {
  return `"${value
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')
    .replace(/\r/g, "\\r")
    .replace(/\n/g, "\\n")}"`;
}

function unquoteYamlScalar(value: string): string {
  const trimmed = value.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    const inner = trimmed.slice(1, -1);
    if (trimmed.startsWith("'")) return inner.replace(/''/g, "'");
    return inner.replace(/\\(["\\rn])/g, (_match, char: string) => {
      if (char === "r") return "\r";
      if (char === "n") return "\n";
      return char;
    });
  }
  return trimmed;
}

function regexValidationMessage(pattern: string, label: string): string | null {
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

export function validateHarnessRuleDrafts(
  rules: AdminHarnessRuleDraft[],
): HarnessRuleValidationError[] {
  const errors: HarnessRuleValidationError[] = [];
  if (rules.length > MAX_RULES) {
    errors.push({
      index: -1,
      field: "rules",
      message: `too many rules; max ${MAX_RULES}`,
    });
  }

  rules.forEach((rule, index) => {
    if (!/^[a-z0-9][a-z0-9._:-]{0,79}$/i.test(rule.id.trim())) {
      errors.push({ index, field: "id", message: "id must be 1-80 URL-safe characters" });
    }
    if (rule.trigger !== "beforeCommit" && rule.trigger !== "afterToolUse") {
      errors.push({ index, field: "trigger", message: "unsupported trigger" });
    }
    if (rule.enforcement !== "block_on_fail" && rule.enforcement !== "audit") {
      errors.push({ index, field: "enforcement", message: "unsupported enforcement" });
    }
    if (rule.inputPath.trim().length === 0 || rule.inputPath.length > MAX_INPUT_PATH_CHARS) {
      errors.push({ index, field: "inputPath", message: "input path is missing or too long" });
    }
    if (!/^[A-Za-z0-9_.-]+$/.test(rule.inputPath.trim())) {
      errors.push({ index, field: "inputPath", message: "input path must be a dot path" });
    }
    if (rule.toolName.trim().length === 0 || rule.toolName.length > 80) {
      errors.push({ index, field: "toolName", message: "tool name is missing or too long" });
    }

    const userMessageError = regexValidationMessage(
      rule.userMessageMatches.trim(),
      "user_message_matches",
    );
    if (userMessageError) {
      errors.push({ index, field: "userMessageMatches", message: userMessageError });
    }
    const inputError = regexValidationMessage(
      rule.inputPattern.trim(),
      rule.inputPath.trim() === "command" ? "input_command_matches" : "pattern",
    );
    if (inputError) {
      errors.push({ index, field: "inputPattern", message: inputError });
    }
  });

  return errors;
}

export function serializeHarnessRulesYaml(rules: AdminHarnessRuleDraft[]): string {
  if (rules.length === 0) return "harness_rules: []";
  const lines = ["harness_rules:"];
  for (const rule of rules) {
    lines.push(`  - id: ${rule.id.trim()}`);
    lines.push(`    enabled: ${rule.enabled ? "true" : "false"}`);
    if (rule.description?.trim()) {
      lines.push(`    description: ${yamlQuote(rule.description.trim())}`);
    }
    lines.push(`    trigger: ${rule.trigger}`);
    lines.push("    when:");
    lines.push(`      user_message_matches: ${yamlQuote(rule.userMessageMatches.trim())}`);
    lines.push("    require:");
    lines.push(`      tool: ${yamlQuote(rule.toolName.trim())}`);
    if (rule.inputPath.trim() === "command") {
      lines.push(`      input_command_matches: ${yamlQuote(rule.inputPattern.trim())}`);
    } else {
      lines.push(`      input_path: ${yamlQuote(rule.inputPath.trim())}`);
      lines.push(`      pattern: ${yamlQuote(rule.inputPattern.trim())}`);
    }
    lines.push(`    enforcement: ${rule.enforcement}`);
  }
  return lines.join("\n");
}

function harnessBlockRange(lines: string[]): { start: number; end: number } | null {
  const start = lines.findIndex((line) => HARNESS_HEADER_RE.test(line.trim()));
  if (start < 0) return null;

  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim() || line.trimStart().startsWith("#")) continue;
    if (!line.startsWith(" ") && !line.startsWith("\t") && TOP_LEVEL_KEY_RE.test(line)) {
      end = i;
      break;
    }
  }
  return { start, end };
}

export function renderAgentConfigWithHarnessRules(
  existingConfig: string,
  rules: AdminHarnessRuleDraft[],
): string {
  const block = serializeHarnessRulesYaml(rules);
  const cleanExisting = existingConfig.replace(/\r\n/g, "\n").trimEnd();
  if (!cleanExisting) return `${block}\n`;

  const lines = cleanExisting.split("\n");
  const range = harnessBlockRange(lines);
  if (!range) return `${cleanExisting}\n\n${block}\n`;

  const next = [
    ...lines.slice(0, range.start),
    ...block.split("\n"),
    ...lines.slice(range.end),
  ].join("\n");
  return `${next.trimEnd()}\n`;
}

function valueAfterColon(line: string): string {
  const index = line.indexOf(":");
  return index >= 0 ? unquoteYamlScalar(line.slice(index + 1)) : "";
}

export function parseHarnessRulesFromAgentConfig(config: string): AdminHarnessRuleDraft[] {
  const lines = config.replace(/\r\n/g, "\n").split("\n");
  const range = harnessBlockRange(lines);
  if (!range) return [];
  if (lines[range.start].trim() === "harness_rules: []") return [];

  const rules: AdminHarnessRuleDraft[] = [];
  let current: AdminHarnessRuleDraft | null = null;
  let section: "when" | "require" | null = null;

  for (const line of lines.slice(range.start + 1, range.end)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    if (trimmed.startsWith("- id:")) {
      if (current) rules.push(current);
      current = {
        id: valueAfterColon(trimmed.slice(1).trim()),
        enabled: true,
        trigger: "beforeCommit",
        userMessageMatches: "",
        toolName: "",
        inputPath: "command",
        inputPattern: "",
        enforcement: "block_on_fail",
      };
      section = null;
      continue;
    }
    if (!current) continue;
    if (trimmed === "when:") {
      section = "when";
      continue;
    }
    if (trimmed === "require:" || trimmed === "action:") {
      section = "require";
      continue;
    }

    if (section === "when" && trimmed.startsWith("user_message_matches:")) {
      current.userMessageMatches = valueAfterColon(trimmed);
      continue;
    }
    if (section === "require") {
      if (trimmed.startsWith("tool:")) current.toolName = valueAfterColon(trimmed);
      else if (trimmed.startsWith("input_path:")) current.inputPath = valueAfterColon(trimmed);
      else if (trimmed.startsWith("input_command_matches:")) {
        current.inputPath = "command";
        current.inputPattern = valueAfterColon(trimmed);
      } else if (trimmed.startsWith("pattern:")) {
        current.inputPattern = valueAfterColon(trimmed);
      }
      continue;
    }

    if (trimmed.startsWith("enabled:")) {
      current.enabled = valueAfterColon(trimmed) !== "false";
    } else if (trimmed.startsWith("description:")) {
      current.description = valueAfterColon(trimmed);
    } else if (trimmed.startsWith("trigger:")) {
      const trigger = valueAfterColon(trimmed);
      if (trigger === "beforeCommit" || trigger === "afterToolUse") current.trigger = trigger;
    } else if (trimmed.startsWith("enforcement:")) {
      const enforcement = valueAfterColon(trimmed);
      if (enforcement === "block_on_fail" || enforcement === "audit") {
        current.enforcement = enforcement;
      }
    }
  }
  if (current) rules.push(current);
  return rules.filter((rule) => rule.id.trim().length > 0);
}

export function assertAgentConfigContentSafe(content: string): void {
  if (content.length > MAX_AGENT_CONFIG_CHARS) {
    throw new Error("agent.config.yaml content too large");
  }
  if (content.includes("\u0000")) {
    throw new Error("agent.config.yaml content contains NUL bytes");
  }
}
