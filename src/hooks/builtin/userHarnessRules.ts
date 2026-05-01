/**
 * User harness rules.
 *
 * OSS users configure these as Markdown files (`USER-RULES.md`,
 * `USER-HARNESS-RULES.md`, or `harness-rules/*.md`). PolicyKernel
 * compiles only a conservative typed subset into HarnessRule objects.
 * This hook executes that typed subset. It never runs arbitrary code,
 * never exposes tools to verifier calls, and never treats unknown
 * natural-language text as executable policy.
 */

import type { HookContext, RegisteredHook } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { PolicyKernel } from "../../policy/PolicyKernel.js";
import type { HarnessRule } from "../../policy/policyTypes.js";

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
  const raw = process.env.CORE_AGENT_USER_HARNESS_RULES;
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
  const successfulIds = new Set<string>();
  for (const entry of transcript) {
    if (entry.turnId !== turnId) continue;
    if (isSuccessfulResult(entry)) successfulIds.add(entry.toolUseId);
  }

  const names = new Set<string>();
  for (const entry of transcript) {
    if (entry.turnId !== turnId) continue;
    if (entry.kind !== "tool_call") continue;
    if (successfulIds.has(entry.toolUseId)) names.add(entry.name);
  }
  return names;
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
    "You are a runtime verifier for a user harness rule.",
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
    `[RETRY:USER_HARNESS_RULE:${rule.id}] A user harness rule failed.`,
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

  if (rule.action.type === "block") {
    return { pass: false, detail: rule.action.reason };
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
