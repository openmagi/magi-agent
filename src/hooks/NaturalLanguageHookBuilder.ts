/**
 * NaturalLanguageHookBuilder — converts plain-language rule descriptions
 * into valid hook configurations.
 *
 * Accepts Korean or English input, uses a fast LLM (Haiku-class) to
 * parse intent, and produces:
 *   - A hook TypeScript file (for complex logic)
 *   - A classifier dimension YAML entry (for simple classification rules)
 *   - Ready-to-paste magi.config.yaml snippets
 *   - A test fixture
 *
 * Design: the builder is stateless — each call produces a self-contained
 * GeneratedHookConfig. No filesystem side-effects; the caller (CLI or
 * HTTP handler) decides what to write.
 */

import type { HookPoint } from "./types.js";

/* ------------------------------------------------------------------ */
/*  Public types                                                       */
/* ------------------------------------------------------------------ */

export interface NaturalLanguageRule {
  /** User's natural language rule description (Korean or English). */
  description: string;
  /** Auto-detected from description when omitted. */
  language?: "ko" | "en";
}

export interface ClassifierDimensionConfig {
  name: string;
  phase: "request" | "final_answer";
  prompt: string;
  output_schema: Record<string, string>;
}

export interface GeneratedHookConfig {
  /** Auto-generated from description (kebab-case). */
  name: string;
  /** Inferred from intent. */
  point: HookPoint;
  /** Suggested based on category. */
  priority: number;
  /** Inferred from "block" vs "warn" intent. */
  blocking: boolean;
  /** Generated TypeScript source. */
  hookCode: string;
  /** If simple enough for classifier, this is populated. */
  classifierDimension?: ClassifierDimensionConfig;
  /** Ready-to-paste magi.config.yaml snippet. */
  yamlConfig: string;
  /** Test fixture YAML. */
  fixtureYaml: string;
}

/**
 * Minimal LLM interface — only needs a single-message completion.
 * Compatible with the project's LLMClient but decoupled for testability.
 */
export interface NLHookLLM {
  /**
   * Send a system + user message and return the assistant text.
   * The builder expects JSON in the response body.
   */
  complete(system: string, user: string): Promise<string>;
}

/* ------------------------------------------------------------------ */
/*  Intent parsing                                                     */
/* ------------------------------------------------------------------ */

/** Structure returned by the LLM intent parse. */
interface ParsedIntent {
  name: string;
  category: "safety" | "compliance" | "moderation" | "custom";
  hookPoint: HookPoint;
  blocking: boolean;
  description_en: string;
  checkLogic: string;
  isSimpleClassifier: boolean;
  classifierPrompt?: string;
  classifierOutputFields?: Record<string, string>;
}

const VALID_HOOK_POINTS = new Set<string>([
  "beforeTurnStart",
  "afterTurnEnd",
  "beforeLLMCall",
  "afterLLMCall",
  "beforeToolUse",
  "afterToolUse",
  "beforeCommit",
  "afterCommit",
  "onAbort",
  "onError",
  "onTaskCheckpoint",
  "beforeCompaction",
  "afterCompaction",
  "onRuleViolation",
  "onArtifactCreated",
]);

const SYSTEM_PROMPT = `You are a hook configuration generator for magi-agent, an autonomous task runtime.

Given a natural language rule description (in Korean or English), you must:

1. Generate a short kebab-case name for the hook (max 40 chars).
2. Categorize the rule: "safety", "compliance", "moderation", or "custom".
3. Determine the best hook point:
   - "beforeCommit" — for output/response gates (block or modify the assistant's final text)
   - "beforeToolUse" — for action gates (block or modify tool executions)
   - "afterToolUse" — for result inspection (check tool results)
   - "beforeLLMCall" — for input modification (add system instructions)
   - "afterLLMCall" — for raw LLM output inspection
   - "beforeTurnStart" — for user input gates
   - "afterTurnEnd" — for turn-level auditing
   - "onTaskCheckpoint" — for memory/logging hooks
4. Determine if the rule should be blocking (true = abort on violation) or non-blocking (false = warn only).
5. Provide the English description of what the hook checks.
6. Describe the check logic in one sentence.
7. Determine if this is simple enough for a classifier dimension (pattern matching on text content = yes; complex tool/state logic = no).
8. If simple, provide a classifier prompt and output fields.

Respond with ONLY valid JSON (no markdown fences):

{
  "name": "string (kebab-case)",
  "category": "safety" | "compliance" | "moderation" | "custom",
  "hookPoint": "string (one of the hook points above)",
  "blocking": true | false,
  "description_en": "string",
  "checkLogic": "string",
  "isSimpleClassifier": true | false,
  "classifierPrompt": "string (only if isSimpleClassifier is true)",
  "classifierOutputFields": { "field": "type description" }
}`;

function detectLanguage(text: string): "ko" | "en" {
  // Simple heuristic: if text contains Korean characters, it's Korean
  const koreanRegex = /[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]/;
  return koreanRegex.test(text) ? "ko" : "en";
}

function parseIntentResponse(raw: string): ParsedIntent {
  // Strip markdown fences if the LLM adds them despite instructions
  const cleaned = raw
    .replace(/^```(?:json)?\s*/m, "")
    .replace(/\s*```\s*$/m, "")
    .trim();

  const parsed = JSON.parse(cleaned) as Record<string, unknown>;

  const name = typeof parsed.name === "string" ? parsed.name : "custom-hook";
  const category = validateCategory(parsed.category);
  const hookPoint = validateHookPoint(parsed.hookPoint);
  const blocking =
    typeof parsed.blocking === "boolean" ? parsed.blocking : true;
  const descEn =
    typeof parsed.description_en === "string"
      ? parsed.description_en
      : "Custom hook";
  const checkLogic =
    typeof parsed.checkLogic === "string"
      ? parsed.checkLogic
      : "Check the condition";
  const isSimple =
    typeof parsed.isSimpleClassifier === "boolean"
      ? parsed.isSimpleClassifier
      : false;

  const result: ParsedIntent = {
    name: sanitizeName(name),
    category,
    hookPoint,
    blocking,
    description_en: descEn,
    checkLogic,
    isSimpleClassifier: isSimple,
  };

  if (isSimple) {
    if (typeof parsed.classifierPrompt === "string") {
      result.classifierPrompt = parsed.classifierPrompt;
    }
    if (
      parsed.classifierOutputFields &&
      typeof parsed.classifierOutputFields === "object"
    ) {
      const fields: Record<string, string> = {};
      for (const [k, v] of Object.entries(
        parsed.classifierOutputFields as Record<string, unknown>,
      )) {
        if (typeof v === "string") fields[k] = v;
      }
      result.classifierOutputFields = fields;
    }
  }

  return result;
}

function sanitizeName(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40);
}

function validateCategory(
  raw: unknown,
): "safety" | "compliance" | "moderation" | "custom" {
  const valid = new Set(["safety", "compliance", "moderation", "custom"]);
  return typeof raw === "string" && valid.has(raw)
    ? (raw as "safety" | "compliance" | "moderation" | "custom")
    : "custom";
}

function validateHookPoint(raw: unknown): HookPoint {
  return typeof raw === "string" && VALID_HOOK_POINTS.has(raw)
    ? (raw as HookPoint)
    : "beforeCommit";
}

/* ------------------------------------------------------------------ */
/*  Code generation                                                    */
/* ------------------------------------------------------------------ */

const CATEGORY_PRIORITIES: Record<string, number> = {
  safety: 10,
  compliance: 20,
  moderation: 30,
  custom: 100,
};

function generateHookCode(intent: ParsedIntent): string {
  const priority = CATEGORY_PRIORITIES[intent.category] ?? 100;
  const action = intent.blocking ? '"block"' : '"continue"';
  const actionResult = intent.blocking
    ? `{ action: ${action}, reason: "${escapeString(intent.description_en)}" }`
    : `{ action: "continue" }`;
  const warnLine = intent.blocking
    ? ""
    : `\n    ctx.log("warn", \`${escapeString(intent.description_en)}: violation detected\`, { point: "${intent.hookPoint}" });`;

  return `/**
 * ${intent.name} — ${intent.description_en}
 *
 * Category: ${intent.category}
 * Generated by NaturalLanguageHookBuilder.
 */

import type {
  HookArgs,
  HookContext,
  HookResult,
  RegisteredHook,
} from "magi-agent/hooks/types";

const hook: RegisteredHook<"${intent.hookPoint}"> = {
  name: "${intent.name}",
  point: "${intent.hookPoint}",
  priority: ${priority},
  blocking: ${intent.blocking},
  timeoutMs: 10_000,
  failOpen: ${!intent.blocking},

  async handler(
    args: HookArgs["${intent.hookPoint}"],
    ctx: HookContext,
  ): Promise<HookResult<HookArgs["${intent.hookPoint}"]> | void> {
    // ${intent.checkLogic}
    //
    // TODO: Replace this LLM-based check with deterministic logic where
    // possible. LLM classification adds latency and cost to every turn.
    const prompt = ${JSON.stringify(buildCheckPrompt(intent))};
    const input = JSON.stringify(args);

    try {
      const response = await ctx.llm.ask(
        \`\${prompt}\\n\\nInput:\\n\${input}\`,
        "claude-haiku-4-5-20251001",
      );
      const result = JSON.parse(response);

      if (result.violation === true) {${warnLine}
        return ${actionResult};
      }

      return { action: "continue" };
    } catch (err) {
      ctx.log("error", \`${intent.name} classification failed\`, {
        error: String(err),
      });
      // Fail-open: allow the turn to proceed if classification fails
      return { action: "continue" };
    }
  },
};

export default hook;
`;
}

function buildCheckPrompt(intent: ParsedIntent): string {
  return [
    `You are a ${intent.category} classifier.`,
    `Check: ${intent.checkLogic}`,
    `Respond with JSON: {"violation": true/false, "reason": "..."}`,
    `Be strict but avoid false positives.`,
  ].join("\n");
}

function escapeString(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

/* ------------------------------------------------------------------ */
/*  YAML generation                                                    */
/* ------------------------------------------------------------------ */

function generateYamlConfig(
  intent: ParsedIntent,
  classifierDim?: ClassifierDimensionConfig,
): string {
  const lines: string[] = [];
  const priority = CATEGORY_PRIORITIES[intent.category] ?? 100;

  lines.push("# Generated by: magi hook create-from-rule");
  lines.push(`# Rule: ${intent.description_en}`);
  lines.push("");
  lines.push("hooks:");
  lines.push("  overrides:");
  lines.push(`    ${intent.name}:`);
  lines.push(`      enabled: true`);
  lines.push(`      priority: ${priority}`);
  lines.push(`      blocking: ${intent.blocking}`);

  if (classifierDim) {
    lines.push("");
    lines.push("classifier:");
    lines.push("  custom_dimensions:");
    lines.push(`    ${classifierDim.name}:`);
    lines.push(`      phase: "${classifierDim.phase}"`);
    lines.push(`      prompt: |`);
    for (const pLine of classifierDim.prompt.split("\n")) {
      lines.push(`        ${pLine}`);
    }
    lines.push("      output_schema:");
    for (const [k, v] of Object.entries(classifierDim.output_schema)) {
      lines.push(`        ${k}: "${v}"`);
    }
  }

  return lines.join("\n") + "\n";
}

function generateFixtureYaml(intent: ParsedIntent): string {
  const lines: string[] = [];

  lines.push(`# ${intent.name} — test fixture`);
  lines.push(`# Generated by: magi hook create-from-rule`);
  lines.push("#");
  lines.push(`# Run with: magi hook test ${intent.name}`);
  lines.push("");
  lines.push(`description: "${intent.description_en} — basic test"`);
  lines.push(`point: "${intent.hookPoint}"`);
  lines.push("input:");
  lines.push(getFixtureInput(intent.hookPoint));
  lines.push("expected:");
  lines.push('  action: "continue"');

  return lines.join("\n") + "\n";
}

function getFixtureInput(point: HookPoint): string {
  switch (point) {
    case "beforeCommit":
      return '  assistantText: "This is a safe response."\n  toolCallCount: 0\n  toolReadHappened: false\n  userMessage: "test"\n  retryCount: 0';
    case "beforeToolUse":
      return '  toolName: "Bash"\n  toolUseId: "tu-1"\n  input: { command: "echo test" }';
    case "beforeLLMCall":
      return '  messages: []\n  tools: []\n  system: "test"\n  iteration: 0';
    case "beforeTurnStart":
      return '  userMessage: "test"';
    case "afterTurnEnd":
      return '  userMessage: "test"\n  assistantText: "Response"\n  status: "committed"';
    case "afterToolUse":
      return '  toolName: "Bash"\n  toolUseId: "tu-1"\n  input: { command: "echo test" }\n  result: { output: "test" }';
    case "afterLLMCall":
      return '  messages: []\n  tools: []\n  system: "test"\n  iteration: 0\n  stopReason: "end_turn"\n  assistantBlocks: []';
    case "onTaskCheckpoint":
      return '  userMessage: "test"\n  assistantText: "done"\n  toolCallCount: 0\n  toolNames: []\n  filesChanged: []\n  startedAt: 0\n  endedAt: 1000';
    default:
      return "  # Add input fields for this hook point";
  }
}

/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */

/**
 * Convert a natural language rule description into a complete hook
 * configuration. Uses the provided LLM to parse intent.
 *
 * @throws {Error} on LLM failure or unparseable response.
 */
export async function buildHookFromNaturalLanguage(
  rule: NaturalLanguageRule,
  llm: NLHookLLM,
): Promise<GeneratedHookConfig> {
  const language = rule.language ?? detectLanguage(rule.description);

  const userMessage = [
    `Rule description (${language === "ko" ? "Korean" : "English"}):`,
    rule.description,
  ].join("\n");

  const raw = await llm.complete(SYSTEM_PROMPT, userMessage);
  const intent = parseIntentResponse(raw);

  // Build classifier dimension if applicable
  let classifierDimension: ClassifierDimensionConfig | undefined;
  if (
    intent.isSimpleClassifier &&
    intent.classifierPrompt &&
    intent.classifierOutputFields &&
    Object.keys(intent.classifierOutputFields).length > 0
  ) {
    const phase: "request" | "final_answer" =
      intent.hookPoint === "beforeTurnStart" ||
      intent.hookPoint === "beforeLLMCall"
        ? "request"
        : "final_answer";

    classifierDimension = {
      name: intent.name,
      phase,
      prompt: intent.classifierPrompt,
      output_schema: intent.classifierOutputFields,
    };
  }

  const priority = CATEGORY_PRIORITIES[intent.category] ?? 100;
  const hookCode = generateHookCode(intent);
  const yamlConfig = generateYamlConfig(intent, classifierDimension);
  const fixtureYaml = generateFixtureYaml(intent);

  return {
    name: intent.name,
    point: intent.hookPoint,
    priority,
    blocking: intent.blocking,
    hookCode,
    classifierDimension,
    yamlConfig,
    fixtureYaml,
  };
}

/* ------------------------------------------------------------------ */
/*  Exported for testing                                               */
/* ------------------------------------------------------------------ */

export {
  detectLanguage,
  sanitizeName,
  parseIntentResponse,
  generateHookCode,
  generateYamlConfig,
  generateFixtureYaml,
  SYSTEM_PROMPT,
};
export type { ParsedIntent };
