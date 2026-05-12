import type { HookContext, RegisteredHook } from "../types.js";
import {
  createDefaultSecurityAnalyzers,
  EnsembleAnalyzer,
  isEnvOn,
  type AnalysisContext,
  type EnsembleVerdict,
} from "../../security/EnsembleAnalyzer.js";

export interface EnsembleSecurityHookOptions {
  workspaceRoot: string;
  propagateUnknown?: boolean;
  timeoutMs?: number;
}

export interface EnsembleSecurityHooks {
  beforeToolUse: RegisteredHook<"beforeToolUse">;
  beforeCommit: RegisteredHook<"beforeCommit">;
}

const DEFAULT_TIMEOUT_MS = 3_000;

export function makeEnsembleSecurityHooks(
  options: EnsembleSecurityHookOptions,
): EnsembleSecurityHooks {
  const timeoutMs = options.timeoutMs ?? envTimeoutMs();
  const propagateUnknown =
    options.propagateUnknown ?? isEnvOn(process.env.MAGI_ENSEMBLE_PROPAGATE_UNKNOWN);

  return {
    beforeToolUse: {
      name: "builtin:ensemble-security-analyzer",
      point: "beforeToolUse",
      priority: 38,
      blocking: true,
      timeoutMs: timeoutMs + 250,
      handler: async ({ toolName, toolUseId, input }, ctx) => {
        const content = toolContent(toolName, input);
        const verdict = await runEnsemble(
          {
            hookPoint: "beforeToolUse",
            content,
            hookContext: ctx,
            toolName,
            toolUseId,
            input,
          },
          options,
          propagateUnknown,
          timeoutMs,
        );
        emitVerdict(ctx, verdict, "beforeToolUse");

        if (verdict.finalSeverity === "pass") return { action: "continue" };
        if (verdict.finalSeverity === "ask") {
          return {
            action: "permission_decision",
            decision: "ask",
            reason: ensembleReason(verdict, "Allow this tool call to proceed?"),
          };
        }
        return {
          action: "permission_decision",
          decision: "deny",
          reason: ensembleReason(verdict, "Tool call denied by ensemble security."),
        };
      },
    },
    beforeCommit: {
      name: "builtin:ensemble-security-analyzer",
      point: "beforeCommit",
      priority: 79,
      blocking: true,
      timeoutMs: timeoutMs + 250,
      handler: async (args, ctx) => {
        const verdict = await runEnsemble(
          {
            hookPoint: "beforeCommit",
            content: args.assistantText,
            hookContext: ctx,
            assistantText: args.assistantText,
            userMessage: args.userMessage,
            toolCallCount: args.toolCallCount,
            toolReadHappened: args.toolReadHappened,
            retryCount: args.retryCount,
            filesChanged: args.filesChanged,
          },
          options,
          propagateUnknown,
          timeoutMs,
        );
        emitVerdict(ctx, verdict, "beforeCommit");

        if (verdict.finalSeverity === "pass") return { action: "continue" };
        return {
          action: "block",
          reason: [
            "[RETRY:ENSEMBLE_SECURITY]",
            ensembleReason(verdict, "Security ensemble blocked this draft."),
            "Rewrite the answer so it does not expose secrets, violate source authority, or include unsafe instructions.",
          ].join("\n"),
        };
      },
    },
  };
}

async function runEnsemble(
  context: AnalysisContext,
  options: EnsembleSecurityHookOptions,
  propagateUnknown: boolean,
  timeoutMs: number,
): Promise<EnsembleVerdict> {
  const analyzers = createDefaultSecurityAnalyzers({
    hookPoint: context.hookPoint,
    workspaceRoot: options.workspaceRoot,
  });
  return await new EnsembleAnalyzer({
    analyzers,
    propagateUnknown,
    timeoutMs,
  }).analyze(context);
}

function emitVerdict(
  ctx: HookContext,
  verdict: EnsembleVerdict,
  phase: "beforeToolUse" | "beforeCommit",
): void {
  const detail = `phase=${phase} severity=${verdict.finalSeverity} verdicts=${verdict.verdicts
    .map((v) => `${v.analyzerName}:${v.severity}`)
    .join(",")}`;
  ctx.emit({
    type: "rule_check",
    ruleId: "ensemble-security-analyzer",
    verdict: verdict.finalSeverity === "pass" ? "ok" : "violation",
    detail,
  });
  if (verdict.finalSeverity !== "pass" || verdict.errors.length > 0) {
    ctx.log("warn", "[ensemble-security] verdict", {
      turnId: ctx.turnId,
      phase,
      finalSeverity: verdict.finalSeverity,
      verdicts: verdict.verdicts.map((v) => ({
        analyzerName: v.analyzerName,
        severity: v.severity,
        confidence: v.confidence,
        reason: v.reason,
      })),
      errors: verdict.errors,
      propagatedUnknown: verdict.propagatedUnknown,
    });
  }
}

function ensembleReason(verdict: EnsembleVerdict, fallback: string): string {
  const strongest = strongestVerdict(verdict);
  const suffix = strongest
    ? `${strongest.analyzerName}: ${strongest.reason}`
    : fallback;
  if (verdict.finalSeverity === "unknown") {
    return `[ENSEMBLE_SECURITY:UNKNOWN] ${suffix}`;
  }
  return `[ENSEMBLE_SECURITY:${verdict.finalSeverity.toUpperCase()}] ${suffix}`;
}

function strongestVerdict(verdict: EnsembleVerdict) {
  const rank = { pass: 0, ask: 1, deny: 2, unknown: 3 } as const;
  return [...verdict.verdicts].sort(
    (a, b) => rank[b.severity] - rank[a.severity],
  )[0];
}

function toolContent(toolName: string, input: unknown): string {
  if (input && typeof input === "object") {
    const obj = input as Record<string, unknown>;
    if (toolName === "Bash" && typeof obj["command"] === "string") {
      return obj["command"];
    }
    if (typeof obj["path"] === "string") return obj["path"];
  }
  try {
    return JSON.stringify(input ?? "");
  } catch {
    return String(input ?? "");
  }
}

function envTimeoutMs(): number {
  const parsed = Number.parseInt(process.env.MAGI_ENSEMBLE_ANALYZER_TIMEOUT_MS ?? "", 10);
  if (Number.isFinite(parsed) && parsed > 0) return parsed;
  return DEFAULT_TIMEOUT_MS;
}
