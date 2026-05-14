import type { RegisteredHook, HookContext } from "../hooks/types.js";
import { isEnvOn, type AnalysisSeverity } from "./EnsembleAnalyzer.js";

export interface CriticScore {
  score: number;
  reason: string;
  suggestions: string[];
  scoredBy: string;
}

export interface CriticInput {
  assistantText: string;
  userMessage: string;
  toolCallCount: number;
  toolReadHappened: boolean;
  filesChanged?: string[];
  turnId: string;
  hookVerdicts?: Record<string, "ok" | "violation">;
  ensembleSeverity?: AnalysisSeverity;
}

export interface CriticScorer {
  name: string;
  score(input: CriticInput): Promise<CriticScore>;
}

export interface CriticConfig {
  threshold: number;
  maxRetries: number;
  scorer: CriticScorer | CriticScorer[];
  buildFollowup: (score: CriticScore, userMessage: string) => string;
}

function isEnabled(): boolean {
  return isEnvOn(process.env.MAGI_CRITIC_GATE);
}

async function runScorers(
  scorers: CriticScorer[],
  input: CriticInput,
): Promise<CriticScore> {
  if (scorers.length === 0) {
    return { score: 1.0, reason: "no scorers", suggestions: [], scoredBy: "none" };
  }
  if (scorers.length === 1) {
    return scorers[0]!.score(input);
  }

  const results = await Promise.all(scorers.map((s) => s.score(input)));
  let min = results[0]!;
  for (let i = 1; i < results.length; i++) {
    if (results[i]!.score < min.score) min = results[i]!;
  }
  return min;
}

export function makeCriticGateHook(config: CriticConfig): RegisteredHook<"beforeCommit"> {
  const scorers = Array.isArray(config.scorer) ? config.scorer : [config.scorer];

  return {
    name: "builtin:critic-gate",
    point: "beforeCommit",
    priority: 92,
    blocking: true,
    failOpen: true,
    timeoutMs: 10_000,
    handler: async (
      { assistantText, userMessage, toolCallCount, toolReadHappened, retryCount, filesChanged },
      ctx: HookContext,
    ) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!assistantText || assistantText.trim().length === 0) {
          return { action: "continue" };
        }

        const input: CriticInput = {
          assistantText,
          userMessage,
          toolCallCount,
          toolReadHappened,
          filesChanged,
          turnId: ctx.turnId,
        };

        const result = await runScorers(scorers, input);

        if (result.score >= config.threshold) {
          ctx.emit({
            type: "rule_check",
            ruleId: "critic-gate",
            verdict: "ok",
            detail: `score=${result.score} threshold=${config.threshold} scoredBy=${result.scoredBy}`,
          });
          return { action: "continue" };
        }

        ctx.emit({
          type: "rule_check",
          ruleId: "critic-gate",
          verdict: "violation",
          detail: `score=${result.score} threshold=${config.threshold} scoredBy=${result.scoredBy} retryCount=${retryCount}`,
        });

        if (retryCount >= config.maxRetries) {
          ctx.log(
            "warn",
            "[critic-gate] retry budget exhausted; failing open",
            { score: result.score, retryCount, scoredBy: result.scoredBy },
          );
          return { action: "continue" };
        }

        ctx.log(
          "warn",
          "[critic-gate] blocking commit: score below threshold",
          { score: result.score, threshold: config.threshold, retryCount, scoredBy: result.scoredBy },
        );

        const followup = config.buildFollowup(result, userMessage);
        return { action: "block", reason: followup };
      } catch (err) {
        ctx.log(
          "warn",
          "[critic-gate] unexpected error; failing open",
          { error: err instanceof Error ? err.message : String(err) },
        );
        return { action: "continue" };
      }
    },
  };
}

export class HallucinationScorer implements CriticScorer {
  readonly name = "hallucination-scorer";

  scoreSync(input: CriticInput): CriticScore {
    const verdicts = input.hookVerdicts ?? {};
    const fg = verdicts["fact-grounding-verifier"];
    const rc = verdicts["resource-existence-checker"];

    if (!fg && !rc) {
      return { score: 1.0, reason: "no upstream verdicts", suggestions: [], scoredBy: this.name };
    }

    let score = 1.0;
    const suggestions: string[] = [];

    if (fg === "violation") {
      score -= 0.6;
      suggestions.push("Re-read tool output and correct distorted claims");
    }
    if (rc === "violation") {
      score -= 0.5;
      suggestions.push("Read referenced files before claiming their contents");
    }

    return {
      score: Math.max(0, score),
      reason: fg === "violation" ? "fact grounding violation" : rc === "violation" ? "resource existence violation" : "grounded",
      suggestions,
      scoredBy: this.name,
    };
  }

  async score(input: CriticInput): Promise<CriticScore> {
    return this.scoreSync(input);
  }
}

export class CompletionScorer implements CriticScorer {
  readonly name = "completion-scorer";

  scoreSync(input: CriticInput): CriticScore {
    const verdicts = input.hookVerdicts ?? {};
    const ce = verdicts["completion-evidence-gate"];
    const tc = verdicts["task-contract-gate"];

    if (!ce && !tc) {
      return { score: 1.0, reason: "no upstream verdicts", suggestions: [], scoredBy: this.name };
    }

    let score = 1.0;
    const suggestions: string[] = [];

    if (ce === "violation") {
      score -= 0.6;
      suggestions.push("Provide verification evidence before claiming completion");
    }
    if (tc === "violation") {
      score -= 0.5;
      suggestions.push("Fulfill task contract verification requirements");
    }

    return {
      score: Math.max(0, score),
      reason: ce === "violation" ? "completion evidence missing" : tc === "violation" ? "task contract unmet" : "completion verified",
      suggestions,
      scoredBy: this.name,
    };
  }

  async score(input: CriticInput): Promise<CriticScore> {
    return this.scoreSync(input);
  }
}

const SEVERITY_SCORE: Record<AnalysisSeverity, number> = {
  pass: 1.0,
  ask: 0.5,
  deny: 0.0,
  unknown: 0.3,
};

export class SecurityScorer implements CriticScorer {
  readonly name = "security-scorer";

  scoreSync(input: CriticInput): CriticScore {
    const severity = input.ensembleSeverity;
    if (!severity) {
      return { score: 1.0, reason: "no ensemble severity", suggestions: [], scoredBy: this.name };
    }

    const score = SEVERITY_SCORE[severity] ?? 0.3;
    return {
      score,
      reason: `ensemble severity: ${severity}`,
      suggestions: score < 1.0 ? [`Address security ${severity} finding`] : [],
      scoredBy: this.name,
    };
  }

  async score(input: CriticInput): Promise<CriticScore> {
    return this.scoreSync(input);
  }
}
