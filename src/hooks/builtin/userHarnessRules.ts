/**
 * User harness rules.
 *
 * Bot-owner Agent Rules remain natural language in the dashboard, but
 * PolicyKernel compiles a conservative subset into typed HarnessRule
 * objects. This hook executes only that typed subset. It never runs
 * arbitrary code, never exposes tools to verifier calls, and never
 * treats unknown natural-language text as executable policy.
 */

import type { HookContext, RegisteredHook } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { PolicyKernel } from "../../policy/PolicyKernel.js";
import type { HarnessRule, BuiltinPresetId, BuiltinPresetConfig } from "../../policy/policyTypes.js";

const MAX_RETRIES = 1;

export interface UserHarnessRuleAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

export interface UserHarnessRuleOptions {
  policy: Pick<PolicyKernel, "current">;
  agent?: UserHarnessRuleAgent;
}

function isEnabled(): boolean {
  const raw = process.env.MAGI_USER_HARNESS_RULES;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

async function readTranscript(
  opts: UserHarnessRuleOptions,
  ctx: HookContext,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!opts.agent) return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  try {
    const entries = await opts.agent.readSessionTranscript(ctx.sessionKey);
    return entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);
  } catch (err) {
    ctx.log("warn", "[user-harness-rules] transcript read failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  }
}

function isSuccessfulResult(
  entry: TranscriptEntry,
): entry is Extract<TranscriptEntry, { kind: "tool_result" }> {
  if (entry.kind !== "tool_result") return false;
  if (entry.isError === true) return false;
  return !entry.status || entry.status === "ok" || entry.status === "success";
}

function successfulToolNames(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): Set<string> {
  return new Set(
    successfulToolCalls(transcript, turnId).map((entry) => entry.name),
  );
}

function successfulToolCalls(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): Extract<TranscriptEntry, { kind: "tool_call" }>[] {
  const successfulIds = new Set<string>();
  for (const entry of transcript) {
    if (entry.turnId !== turnId) continue;
    if (isSuccessfulResult(entry)) successfulIds.add(entry.toolUseId);
  }

  const calls: Extract<TranscriptEntry, { kind: "tool_call" }>[] = [];
  for (const entry of transcript) {
    if (entry.turnId !== turnId) continue;
    if (entry.kind !== "tool_call") continue;
    if (successfulIds.has(entry.toolUseId)) calls.push(entry);
  }
  return calls;
}

function regexMatches(pattern: string, text: string): boolean {
  try {
    return new RegExp(pattern, "iu").test(text);
  } catch {
    return false;
  }
}

function valueAtInputPath(input: unknown, inputPath: string): unknown {
  const segments = inputPath
    .split(".")
    .map((segment) => segment.trim())
    .filter((segment) => segment.length > 0);
  let current = input;
  for (const segment of segments) {
    if (typeof current !== "object" || current === null) return undefined;
    if (!Object.prototype.hasOwnProperty.call(current, segment)) return undefined;
    current = (current as Record<string, unknown>)[segment];
  }
  return current;
}

function inputValueAsText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function hasSuccessfulToolInputMatch(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
  args: {
    toolName: string;
    inputPath: string;
    pattern: string;
  },
): boolean {
  return successfulToolCalls(transcript, turnId).some((entry) => {
    if (entry.name !== args.toolName) return false;
    const value = valueAtInputPath(entry.input, args.inputPath);
    return regexMatches(args.pattern, inputValueAsText(value));
  });
}

function conditionMatchesBeforeCommit(
  rule: HarnessRule,
  args: {
    userMessage: string;
  },
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): boolean {
  const condition = rule.condition;
  if (!condition) return true;

  if (condition.userMessageIncludes && condition.userMessageIncludes.length > 0) {
    const lower = args.userMessage.toLowerCase();
    if (
      !condition.userMessageIncludes.some((needle) =>
        lower.includes(needle.toLowerCase()),
      )
    ) {
      return false;
    }
  }
  if (
    condition.userMessageMatches &&
    !regexMatches(condition.userMessageMatches, args.userMessage)
  ) {
    return false;
  }

  const tools = successfulToolNames(transcript, turnId);
  if (condition.toolName && !tools.has(condition.toolName)) return false;
  if (condition.anyToolUsed && condition.anyToolUsed.length > 0) {
    return condition.anyToolUsed.some((toolName) => tools.has(toolName));
  }
  return true;
}

function conditionMatchesAfterTool(rule: HarnessRule, toolName: string): boolean {
  const condition = rule.condition;
  if (!condition) return true;
  if (condition.toolName && condition.toolName !== toolName) return false;
  if (condition.anyToolUsed && condition.anyToolUsed.length > 0) {
    return condition.anyToolUsed.includes(toolName);
  }
  return true;
}

function parseVerifierPass(output: string): { pass: boolean; detail: string } {
  const trimmed = output.trim();
  const upper = trimmed.toUpperCase();
  if (upper.startsWith("FAIL")) return { pass: false, detail: trimmed };
  if (upper.startsWith("PASS")) return { pass: true, detail: trimmed || "PASS" };
  return { pass: true, detail: trimmed || "unparseable verifier output; fail-open" };
}

async function runLlmVerifier(
  rule: HarnessRule,
  ctx: HookContext,
  payload: {
    userMessage?: string;
    assistantText?: string;
    toolName?: string;
    toolResult?: unknown;
  },
): Promise<{ pass: boolean; detail: string }> {
  if (rule.action.type !== "llm_verifier") {
    return { pass: true, detail: "not an llm verifier" };
  }

  const prompt = [
    "You are a runtime verifier for a bot-owner harness rule.",
    "Do not use tools. Judge only the supplied text and result summary.",
    "Reply with exactly `PASS` or `FAIL: <short reason>`.",
    "",
    "HARNESS RULE:",
    rule.action.prompt,
    "",
    payload.userMessage ? `USER REQUEST:\n${payload.userMessage.slice(0, 3000)}` : "",
    payload.assistantText
      ? `ASSISTANT FINAL ANSWER:\n${payload.assistantText.slice(0, 5000)}`
      : "",
    payload.toolName ? `TOOL NAME:\n${payload.toolName}` : "",
    payload.toolResult
      ? `TOOL RESULT SUMMARY:\n${JSON.stringify(payload.toolResult).slice(0, 3000)}`
      : "",
  ]
    .filter((line) => line.length > 0)
    .join("\n\n");

  let output = "";
  try {
    const stream = ctx.llm.stream({
      model: ctx.agentModel,
      system:
        "Runtime verifier. Return only PASS or FAIL with a brief reason. No tools.",
      messages: [{ role: "user", content: prompt }],
      max_tokens: 80,
      temperature: 0,
      signal: ctx.abortSignal,
    });
    const deadline = Date.now() + Math.max(500, rule.timeoutMs);
    for await (const event of stream) {
      if (Date.now() > deadline) break;
      if (event.kind === "text_delta") output += event.delta;
      if (event.kind === "message_end" || event.kind === "error") break;
    }
  } catch (err) {
    ctx.log("warn", "[user-harness-rules] verifier failed open", {
      ruleId: rule.id,
      error: err instanceof Error ? err.message : String(err),
    });
    return { pass: true, detail: "verifier failed open" };
  }
  return parseVerifierPass(output);
}

function emitRuleCheck(
  ctx: HookContext,
  rule: HarnessRule,
  pass: boolean,
  detail: string,
): void {
  ctx.emit({
    type: "rule_check",
    ruleId: rule.id,
    verdict: pass ? "ok" : "violation",
    detail,
  });
}

function blockReason(rule: HarnessRule, detail: string): string {
  return [
    `[RETRY:USER_HARNESS_RULE:${rule.id}] A bot-owner harness rule failed.`,
    "",
    `Rule: ${rule.sourceText}`,
    `Failure: ${detail}`,
    "",
    "Re-attempt the turn while satisfying this rule. If the rule cannot be satisfied, say that explicitly instead of claiming success.",
  ].join("\n");
}

async function evaluateBeforeCommitRule(
  rule: HarnessRule,
  args: {
    assistantText: string;
    userMessage: string;
    retryCount: number;
  },
  transcript: ReadonlyArray<TranscriptEntry>,
  ctx: HookContext,
): Promise<{ pass: boolean; detail: string }> {
  if (rule.action.type === "require_tool") {
    const tools = successfulToolNames(transcript, ctx.turnId);
    const pass = tools.has(rule.action.toolName);
    return {
      pass,
      detail: pass
        ? `${rule.action.toolName} succeeded in this turn`
        : `required tool ${rule.action.toolName} did not succeed in this turn`,
    };
  }

  if (rule.action.type === "require_tool_input_match") {
    const pass = hasSuccessfulToolInputMatch(transcript, ctx.turnId, {
      toolName: rule.action.toolName,
      inputPath: rule.action.inputPath,
      pattern: rule.action.pattern,
    });
    return {
      pass,
      detail: pass
        ? `${rule.action.toolName} input ${rule.action.inputPath} matched in this turn`
        : `required ${rule.action.toolName} input ${rule.action.inputPath} matching ${rule.action.pattern} did not succeed in this turn`,
    };
  }

  if (rule.action.type === "block") {
    return { pass: false, detail: rule.action.reason };
  }

  if (rule.action.type === "builtin_preset") {
    return executeBuiltinPreset(rule.action.preset, rule.action.config, args, transcript, ctx);
  }

  return runLlmVerifier(rule, ctx, {
    userMessage: args.userMessage,
    assistantText: args.assistantText,
  });
}

export function makeUserHarnessRuleHooks(
  opts: UserHarnessRuleOptions,
): {
  beforeCommit: RegisteredHook<"beforeCommit">;
  afterToolUse: RegisteredHook<"afterToolUse">;
} {
  return {
    beforeCommit: {
      name: "builtin:user-harness-rules",
      point: "beforeCommit",
      priority: 86,
      blocking: true,
      timeoutMs: 10_000,
      handler: async (args, ctx) => {
        if (!isEnabled()) return { action: "continue" };
        const snapshot = await opts.policy.current();
        const rules = snapshot.policy.harnessRules.filter(
          (rule) => rule.enabled && rule.trigger === "beforeCommit",
        );
        if (rules.length === 0) return { action: "continue" };

        const transcript = await readTranscript(opts, ctx);
        for (const rule of rules) {
          if (!conditionMatchesBeforeCommit(rule, args, transcript, ctx.turnId)) {
            continue;
          }

          const result = await evaluateBeforeCommitRule(rule, args, transcript, ctx);
          emitRuleCheck(ctx, rule, result.pass, result.detail);
          if (result.pass || rule.enforcement === "audit") continue;

          if (args.retryCount >= MAX_RETRIES) {
            ctx.log("warn", "[user-harness-rules] retry exhausted; failing open", {
              ruleId: rule.id,
              retryCount: args.retryCount,
              detail: result.detail,
            });
            continue;
          }

          return {
            action: "block",
            reason: blockReason(rule, result.detail),
          };
        }
        return { action: "continue" };
      },
    },
    afterToolUse: {
      name: "builtin:user-harness-rules-after-tool",
      point: "afterToolUse",
      priority: 86,
      blocking: false,
      timeoutMs: 10_000,
      handler: async (args, ctx) => {
        if (!isEnabled()) return { action: "continue" };
        const snapshot = await opts.policy.current();
        const rules = snapshot.policy.harnessRules.filter(
          (rule) =>
            rule.enabled &&
            rule.trigger === "afterToolUse" &&
            conditionMatchesAfterTool(rule, args.toolName),
        );
        for (const rule of rules) {
          if (rule.action.type === "llm_verifier") {
            const result = await runLlmVerifier(rule, ctx, {
              toolName: args.toolName,
              toolResult: args.result,
            });
            emitRuleCheck(ctx, rule, result.pass, result.detail);
            continue;
          }
          if (rule.action.type === "block") {
            emitRuleCheck(ctx, rule, false, rule.action.reason);
            continue;
          }
          emitRuleCheck(ctx, rule, true, "afterToolUse rule observed");
        }
        return { action: "continue" };
      },
    },
  };
}

// ── Builtin Preset Executor ─────────────────────────────────────

async function executeBuiltinPreset(
  preset: BuiltinPresetId,
  config: BuiltinPresetConfig | undefined,
  args: { assistantText: string; userMessage: string; retryCount: number; toolCallCount?: number; toolReadHappened?: boolean; toolNames?: string[] },
  transcript: ReadonlyArray<TranscriptEntry>,
  ctx: HookContext,
): Promise<{ pass: boolean; detail: string }> {
  const mode = config?.mode ?? "hybrid";

  switch (preset) {
    case "answer-quality":
      return executeAnswerQualityPreset(mode, args, ctx);
    case "self-claim":
      return executeSelfClaimPreset(mode, args, ctx);
    case "fact-grounding":
      return executeFactGroundingPreset(mode, args, transcript, ctx);
    case "response-language":
      return executeResponseLanguagePreset(mode, args, ctx);
    case "deterministic-evidence":
      return executeDeterministicEvidencePreset(mode, args, ctx);
    default:
      return { pass: true, detail: `unknown preset: ${preset}` };
  }
}

async function executeAnswerQualityPreset(
  mode: string,
  args: { assistantText: string; userMessage: string },
  ctx: HookContext,
): Promise<{ pass: boolean; detail: string }> {
  const { judgeAnswerDeterministic, judgeAnswer } = await import("./answerVerifier.js");
  if (mode === "deterministic" || mode === "hybrid") {
    const det = judgeAnswerDeterministic(args.userMessage, args.assistantText);
    if (mode === "deterministic" || det.confidence === "high") {
      return { pass: det.verdict === "FULFILLED" || det.verdict === "REFUSAL", detail: `answer-quality: ${det.verdict} (${det.reason})` };
    }
  }
  const verdict = await judgeAnswer(ctx.llm, args.userMessage, args.assistantText, 15_000, ctx.agentModel);
  return { pass: verdict === "FULFILLED" || verdict === "REFUSAL", detail: `answer-quality: ${verdict} (llm)` };
}

async function executeSelfClaimPreset(
  mode: string,
  args: { assistantText: string; userMessage: string; toolReadHappened?: boolean },
  ctx: HookContext,
): Promise<{ pass: boolean; detail: string }> {
  const { detectSelfClaimDeterministic } = await import("./selfClaimVerifier.js");
  const det = detectSelfClaimDeterministic(args.assistantText);

  if (mode === "deterministic" || (mode === "hybrid" && det.confidence === "high")) {
    if (!det.hasClaim) return { pass: true, detail: "self-claim: no claim detected" };
    if (args.toolReadHappened) return { pass: true, detail: "self-claim: claim with tool read" };
    return { pass: false, detail: `self-claim: ${det.reason} without file read` };
  }

  // LLM fallback
  const { getOrClassifyFinalAnswerMeta } = await import("./turnMetaClassifier.js");
  const meta = await getOrClassifyFinalAnswerMeta(ctx, { userMessage: args.userMessage, assistantText: args.assistantText });
  if (!meta.selfClaim) return { pass: true, detail: "self-claim: no claim (llm)" };
  if (args.toolReadHappened) return { pass: true, detail: "self-claim: claim with tool read (llm)" };
  return { pass: false, detail: "self-claim: claim without file read (llm)" };
}

async function executeFactGroundingPreset(
  mode: string,
  args: { assistantText: string; userMessage: string; toolReadHappened?: boolean },
  transcript: ReadonlyArray<TranscriptEntry>,
  ctx: HookContext,
): Promise<{ pass: boolean; detail: string }> {
  const {
    groundAgainstToolResults,
    detectUngroundedFileClaims,
    judgeGrounding,
    judgeUngroundedClaims,
  } = await import("./factGroundingVerifier.js");

  if (!args.toolReadHappened) {
    const det = detectUngroundedFileClaims(args.assistantText);
    if (mode === "deterministic" || (mode === "hybrid" && det.confidence === "high")) {
      const pass = det.verdict !== "FABRICATED";
      return { pass, detail: `fact-grounding-B: ${det.verdict} (${det.reason})` };
    }
    const verdict = await judgeUngroundedClaims(ctx.llm, args.assistantText, 15_000, ctx.agentModel);
    return { pass: verdict !== "FABRICATED", detail: `fact-grounding-B: ${verdict} (llm)` };
  }

  const det = groundAgainstToolResults(transcript, ctx.turnId, args.assistantText);
  if (mode === "deterministic" || (mode === "hybrid" && det.confidence === "high")) {
    return { pass: det.verdict === "GROUNDED", detail: `fact-grounding-A: ${det.verdict} (${det.reason})` };
  }
  // Need buildToolResultsSummary — import it
  const factModule = await import("./factGroundingVerifier.js");
  const summary = (factModule as Record<string, unknown>)["buildToolResultsSummary"] as
    ((transcript: ReadonlyArray<TranscriptEntry>, turnId: string) => string) | undefined;
  const summaryText = summary ? summary(transcript, ctx.turnId) : "";
  if (!summaryText.trim()) return { pass: true, detail: "fact-grounding-A: no tool results" };
  const verdict = await judgeGrounding(ctx.llm, summaryText, args.assistantText, 15_000, ctx.agentModel);
  return { pass: verdict === "GROUNDED", detail: `fact-grounding-A: ${verdict} (llm)` };
}

async function executeResponseLanguagePreset(
  mode: string,
  args: { assistantText: string; userMessage: string },
  ctx: HookContext,
): Promise<{ pass: boolean; detail: string }> {
  const {
    resolveLanguagePolicy,
    detectPrimaryLanguage,
    judgeResponseLanguage,
  } = await import("./responseLanguageGate.js");

  // Load language policy from PolicyKernel is not available here directly,
  // so we detect from user message as fallback
  const resolved = resolveLanguagePolicy("auto", args.userMessage);
  const detected = detectPrimaryLanguage(args.assistantText);

  if (mode === "deterministic" || mode === "hybrid") {
    if (detected === resolved.target || resolved.target === "auto") {
      return { pass: true, detail: `response-language: ${detected ?? "auto"} matches target` };
    }
    if (mode === "deterministic") {
      return detected
        ? { pass: false, detail: `response-language: ${detected} vs target ${resolved.target}` }
        : { pass: true, detail: "response-language: undetectable, pass" };
    }
  }

  const verdict = await judgeResponseLanguage(ctx, {
    language: "auto",
    userMessage: args.userMessage,
    assistantText: args.assistantText,
  });
  return { pass: verdict.pass, detail: `response-language: ${verdict.detail}` };
}

async function executeDeterministicEvidencePreset(
  mode: string,
  args: { assistantText: string; userMessage: string },
  ctx: HookContext,
): Promise<{ pass: boolean; detail: string }> {
  const contract = ctx.executionContract;
  if (!contract) return { pass: true, detail: "deterministic-evidence: no contract" };

  const snapshot = contract.snapshot();
  const requirements = snapshot.taskState.deterministicRequirements.filter(
    (r) => r.turnId === ctx.turnId && r.status !== "waived",
  );
  if (requirements.length === 0) return { pass: true, detail: "deterministic-evidence: no requirements" };

  const reqIds = new Set(requirements.map((r) => r.requirementId));
  const evidence = snapshot.taskState.deterministicEvidence.filter(
    (e) => e.kind !== "verification" && e.status === "passed" && e.requirementIds.some((id) => reqIds.has(id)),
  );
  if (evidence.length === 0) return { pass: false, detail: "deterministic-evidence: no tool evidence" };

  const { judgeDeterministicEvidenceBySchema, judgeDeterministicEvidence } = await import("./deterministicEvidenceVerifier.js");

  if (mode === "deterministic" || mode === "hybrid") {
    const det = judgeDeterministicEvidenceBySchema(args.assistantText, evidence);
    if (mode === "deterministic" || det.confidence === "high") {
      return { pass: det.verdict === "PASS", detail: `deterministic-evidence: ${det.verdict} (${det.reason})` };
    }
  }

  const verdict = await judgeDeterministicEvidence({
    llm: ctx.llm, model: ctx.agentModel,
    userMessage: args.userMessage, assistantText: args.assistantText,
    requirements, evidence,
    timeoutMs: 10_000, signal: ctx.abortSignal,
  });
  return { pass: verdict === "PASS", detail: `deterministic-evidence: ${verdict} (llm)` };
}
