import type {
  HookArgs,
  HookContext,
  HookPoint,
  HookResult,
} from "../hooks/types.js";
import { makeDangerousPatternsHook } from "../hooks/builtin/dangerousPatterns.js";
import { detectSecretExposure } from "../hooks/builtin/secretExposureGate.js";
import { makeSourceAuthorityGateHook } from "../hooks/builtin/sourceAuthorityGate.js";

export type AnalysisSeverity = "pass" | "ask" | "deny" | "unknown";

export interface AnalysisContext {
  hookPoint: Extract<HookPoint, "beforeToolUse" | "beforeCommit">;
  content: string;
  hookContext?: HookContext;
  toolName?: string;
  toolUseId?: string;
  input?: unknown;
  assistantText?: string;
  userMessage?: string;
  toolCallCount?: number;
  toolNames?: string[];
  toolReadHappened?: boolean;
  retryCount?: number;
  filesChanged?: string[];
}

export interface AnalysisVerdict {
  severity: AnalysisSeverity;
  confidence: number;
  reason: string;
  analyzerName: string;
}

export interface SecurityAnalyzer {
  name: string;
  analyze(context: AnalysisContext): Promise<AnalysisVerdict>;
}

export interface EnsembleVerdict {
  finalSeverity: AnalysisSeverity;
  verdicts: AnalysisVerdict[];
  errors: Array<{ analyzerName: string; error: string }>;
  propagatedUnknown: boolean;
}

export interface EnsembleAnalyzerOptions {
  analyzers: readonly SecurityAnalyzer[];
  propagateUnknown?: boolean;
  timeoutMs?: number;
}

const DEFAULT_ANALYZER_TIMEOUT_MS = 3_000;

const SEVERITY_RANK: Record<Exclude<AnalysisSeverity, "unknown">, number> = {
  pass: 0,
  ask: 1,
  deny: 2,
};

export class EnsembleAnalyzer {
  private readonly analyzers: readonly SecurityAnalyzer[];
  private readonly propagateUnknown: boolean;
  private readonly timeoutMs: number;

  constructor(options: EnsembleAnalyzerOptions) {
    this.analyzers = options.analyzers;
    this.propagateUnknown = options.propagateUnknown ?? false;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_ANALYZER_TIMEOUT_MS;
  }

  async analyze(context: AnalysisContext): Promise<EnsembleVerdict> {
    const settled = await Promise.allSettled(
      this.analyzers.map((analyzer) => this.runAnalyzer(analyzer, context)),
    );

    const verdicts: AnalysisVerdict[] = [];
    const errors: EnsembleVerdict["errors"] = [];

    for (let i = 0; i < settled.length; i++) {
      const result = settled[i];
      const analyzerName = this.analyzers[i]?.name ?? `analyzer-${i}`;
      if (!result || result.status === "rejected") {
        const error = errorMessage(result?.reason);
        errors.push({ analyzerName, error });
        verdicts.push({
          severity: "deny",
          confidence: 0,
          reason: `analyzer failed closed: ${error}`,
          analyzerName,
        });
        continue;
      }

      verdicts.push(normalizeVerdict(result.value, analyzerName));
    }

    const hasUnknown = verdicts.some((verdict) => verdict.severity === "unknown");
    const propagatedUnknown = this.propagateUnknown && hasUnknown;

    return {
      finalSeverity: propagatedUnknown ? "unknown" : maxSeverity(verdicts),
      verdicts,
      errors,
      propagatedUnknown,
    };
  }

  private async runAnalyzer(
    analyzer: SecurityAnalyzer,
    context: AnalysisContext,
  ): Promise<AnalysisVerdict> {
    let timer: NodeJS.Timeout | null = null;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(`analyzer timeout after ${this.timeoutMs}ms`)),
        this.timeoutMs,
      );
      timer.unref?.();
    });

    try {
      return await Promise.race([analyzer.analyze(context), timeout]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }
}

export interface CreateDefaultSecurityAnalyzersOptions {
  hookPoint: AnalysisContext["hookPoint"];
  workspaceRoot: string;
  includeSourceAuthority?: boolean;
}

export function createDefaultSecurityAnalyzers(
  options: CreateDefaultSecurityAnalyzersOptions,
): SecurityAnalyzer[] {
  const analyzers: SecurityAnalyzer[] = [];
  if (options.hookPoint === "beforeToolUse") {
    analyzers.push(new PatternAnalyzer({ workspaceRoot: options.workspaceRoot }));
  }
  if (options.hookPoint === "beforeCommit") {
    analyzers.push(new SecretAnalyzer());
    if (options.includeSourceAuthority ?? true) {
      analyzers.push(new SourceAuthorityAnalyzer());
    }
  }
  if (isEnvOn(process.env.MAGI_LLM_ANALYZER)) {
    analyzers.push(new LLMAnalyzer());
  }
  return analyzers;
}

export class PatternAnalyzer implements SecurityAnalyzer {
  readonly name = "pattern-analyzer";
  private readonly hook: ReturnType<typeof makeDangerousPatternsHook>;

  constructor(options: { workspaceRoot: string }) {
    this.hook = makeDangerousPatternsHook({ workspaceRoot: options.workspaceRoot });
  }

  async analyze(context: AnalysisContext): Promise<AnalysisVerdict> {
    if (context.hookPoint !== "beforeToolUse") {
      return pass(this.name, "not a tool-use security context");
    }
    if (!context.hookContext || !context.toolName) {
      return pass(this.name, "missing hook context or tool name");
    }

    const result = await this.hook.handler(
      {
        toolName: context.toolName,
        toolUseId: context.toolUseId ?? "ensemble-security",
        input: context.input,
      },
      context.hookContext,
    );
    return hookResultToVerdict(this.name, result, "dangerous pattern analysis passed");
  }
}

export class SecretAnalyzer implements SecurityAnalyzer {
  readonly name = "secret-analyzer";

  async analyze(context: AnalysisContext): Promise<AnalysisVerdict> {
    if (context.hookPoint !== "beforeCommit") {
      return pass(this.name, "not a commit security context");
    }
    const text = context.assistantText ?? context.content;
    if (!detectSecretExposure(text)) {
      return pass(this.name, "no literal secret exposure detected");
    }
    return {
      severity: "deny",
      confidence: 1,
      reason: "secret-like literal detected in assistant output",
      analyzerName: this.name,
    };
  }
}

export class SourceAuthorityAnalyzer implements SecurityAnalyzer {
  readonly name = "source-authority-analyzer";
  private readonly hook = makeSourceAuthorityGateHook();

  async analyze(context: AnalysisContext): Promise<AnalysisVerdict> {
    if (context.hookPoint !== "beforeCommit") {
      return pass(this.name, "not a commit security context");
    }
    if (!context.hookContext) {
      return pass(this.name, "missing hook context");
    }

    const args: HookArgs["beforeCommit"] = {
      assistantText: context.assistantText ?? context.content,
      userMessage: context.userMessage ?? "",
      toolCallCount: context.toolCallCount ?? 0,
      toolReadHappened: context.toolReadHappened ?? false,
      retryCount: context.retryCount ?? 0,
      toolNames: context.toolNames ?? [],
      filesChanged: context.filesChanged,
    };
    const result = await this.hook.handler(args, context.hookContext);
    return hookResultToVerdict(this.name, result, "source authority analysis passed");
  }
}

export class LLMAnalyzer implements SecurityAnalyzer {
  readonly name = "llm-analyzer";
  private readonly model: string;

  constructor(options: { model?: string } = {}) {
    this.model = options.model ?? "claude-haiku-4-5-20251001";
  }

  async analyze(context: AnalysisContext): Promise<AnalysisVerdict> {
    const hookContext = context.hookContext;
    const llm = hookContext?.llm;
    if (!llm) {
      return {
        severity: "unknown",
        confidence: 0,
        reason: "LLM analyzer has no hook LLM client",
        analyzerName: this.name,
      };
    }

    const output = await collectLLMText(
      llm.stream({
        model: this.model,
        system: [
          "You are a security classifier for tool commands and assistant drafts.",
          "Return compact JSON only: {\"severity\":\"pass|ask|deny|unknown\",\"confidence\":0-1,\"reason\":\"...\"}.",
          "Use deny for credential exfiltration, destructive commands, or clear policy bypass attempts.",
          "Use ask for ambiguous elevated risk. Use pass for benign content.",
        ].join("\n"),
        messages: [
          {
            role: "user",
            content: JSON.stringify({
              hookPoint: context.hookPoint,
              toolName: context.toolName,
              content: context.content.slice(0, 4_000),
            }),
          },
        ],
        max_tokens: 100,
        temperature: 0,
        signal: hookContext.abortSignal,
      }),
    );

    return parseLLMVerdict(output, this.name);
  }
}

function normalizeVerdict(
  verdict: AnalysisVerdict,
  analyzerName: string,
): AnalysisVerdict {
  return {
    severity: verdict.severity,
    confidence: clampConfidence(verdict.confidence),
    reason: verdict.reason,
    analyzerName: verdict.analyzerName || analyzerName,
  };
}

function maxSeverity(verdicts: readonly AnalysisVerdict[]): AnalysisSeverity {
  let max: Exclude<AnalysisSeverity, "unknown"> = "pass";
  for (const verdict of verdicts) {
    if (verdict.severity === "unknown") continue;
    if (SEVERITY_RANK[verdict.severity] > SEVERITY_RANK[max]) {
      max = verdict.severity;
    }
  }
  return max;
}

function hookResultToVerdict(
  analyzerName: string,
  result: HookResult<HookArgs["beforeToolUse"]> | HookResult<HookArgs["beforeCommit"]> | void,
  passReason: string,
): AnalysisVerdict {
  if (!result || result.action === "continue") return pass(analyzerName, passReason);
  if (result.action === "block") {
    return {
      severity: "deny",
      confidence: 1,
      reason: result.reason,
      analyzerName,
    };
  }
  if (result.action === "permission_decision") {
    if (result.decision === "deny") {
      return {
        severity: "deny",
        confidence: 1,
        reason: result.reason ?? "permission denied by wrapped security hook",
        analyzerName,
      };
    }
    if (result.decision === "ask") {
      return {
        severity: "ask",
        confidence: 0.8,
        reason: result.reason ?? "wrapped security hook requested confirmation",
        analyzerName,
      };
    }
  }
  return pass(analyzerName, passReason);
}

function pass(analyzerName: string, reason: string): AnalysisVerdict {
  return { severity: "pass", confidence: 1, reason, analyzerName };
}

function clampConfidence(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function isEnvOn(raw: string | undefined): boolean {
  if (raw === undefined || raw === null) return false;
  const value = raw.trim().toLowerCase();
  return value === "1" || value === "true" || value === "on";
}

async function collectLLMText(
  stream: AsyncIterable<{ kind: string; delta?: string; message?: string }>,
): Promise<string> {
  let output = "";
  for await (const event of stream) {
    if (event.kind === "text_delta") output += event.delta ?? "";
    if (event.kind === "message_end" || event.kind === "error") break;
  }
  return output;
}

function parseLLMVerdict(output: string, analyzerName: string): AnalysisVerdict {
  const parsed = parseJsonObject(output);
  if (!parsed) {
    return {
      severity: "unknown",
      confidence: 0,
      reason: "LLM analyzer returned non-JSON verdict",
      analyzerName,
    };
  }
  const rawSeverity = parsed["severity"];
  const severity: AnalysisSeverity =
    rawSeverity === "pass" ||
    rawSeverity === "ask" ||
    rawSeverity === "deny" ||
    rawSeverity === "unknown"
      ? rawSeverity
      : "unknown";
  const rawConfidence = parsed["confidence"];
  const confidence =
    typeof rawConfidence === "number" ? clampConfidence(rawConfidence) : 0;
  const rawReason = parsed["reason"];
  const reason =
    typeof rawReason === "string" && rawReason.trim()
      ? rawReason.trim()
      : "LLM security verdict";
  return { severity, confidence, reason, analyzerName };
}

function parseJsonObject(raw: string): Record<string, unknown> | null {
  const trimmed = raw.trim();
  const start = trimmed.indexOf("{");
  const end = trimmed.lastIndexOf("}");
  if (start < 0 || end < start) return null;
  try {
    const parsed = JSON.parse(trimmed.slice(start, end + 1));
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}
