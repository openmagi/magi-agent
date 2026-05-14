import type { SourceLedgerRecord } from "../../research/SourceLedger.js";
import type { HookContext, RegisteredHook } from "../types.js";

const MAX_RETRIES = 1;

const BROAD_PARALLEL_RE =
  /\b(?:broad|deep|comprehensive|parallel|multi[-_\s]?source|multi[-_\s]?agent|multi[-_\s]?step|long[-_\s]?running|independent\s+subquestions?|research\s+pipeline)\b.{0,120}\b(?:research|investigat|source|synthesi[sz]e|compare|scout)\b|\b(?:research|investigat|source|synthesi[sz]e|compare|scout)\b.{0,120}\b(?:broad|deep|comprehensive|parallel|multi[-_\s]?source|multi[-_\s]?agent|multi[-_\s]?step|long[-_\s]?running|independent\s+subquestions?)\b|딥\s*리서치|심층\s*(?:조사|리서치|분석)|병렬.{0,40}(?:조사|리서치|분석)|(?:조사|리서치|분석).{0,40}병렬|복수.{0,30}출처|여러.{0,30}출처|다각도.{0,30}(?:조사|리서치|분석)/i;

function isEnabled(): boolean {
  const raw = process.env.MAGI_PARALLEL_RESEARCH_GATE;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function requiresParallelResearch(userMessage: string): boolean {
  return BROAD_PARALLEL_RE.test(userMessage);
}

function isSubagentResult(source: SourceLedgerRecord): boolean {
  return source.kind === "subagent_result";
}

function hasSubagentResult(ctx: HookContext): boolean {
  return (ctx.sourceLedger?.sourcesForTurn(ctx.turnId) ?? []).some(isSubagentResult);
}

export function makeParallelResearchGateHook(): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:parallel-research-gate",
    point: "beforeCommit",
    priority: 80,
    blocking: true,
    failOpen: true,
    timeoutMs: 500,
    handler: async ({ userMessage, retryCount }, ctx) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!requiresParallelResearch(userMessage)) return { action: "continue" };

        if (hasSubagentResult(ctx)) {
          ctx.emit({
            type: "rule_check",
            ruleId: "parallel-research-gate",
            verdict: "ok",
            detail: "subagent research evidence recorded",
          });
          return { action: "continue" };
        }

        ctx.emit({
          type: "rule_check",
          ruleId: "parallel-research-gate",
          verdict: "violation",
          detail: "broad research turn lacks subagent result evidence",
        });

        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[parallel-research-gate] retry exhausted; failing open", {
            turnId: ctx.turnId,
          });
          return { action: "continue" };
        }

        return {
          action: "block",
          reason: [
            "[RETRY:PARALLEL_RESEARCH]",
            "This broad or parallel research answer is missing child-agent evidence.",
            "Use SpawnAgent with persona:\"research\" for at least one independent subquestion before committing the synthesis.",
            "Prefer deliver:\"return\" when the parent answer depends on the child result in this turn.",
            "Require concrete child evidence with completion_contract.required_evidence:\"tool_call\" unless this is a text-only synthesis handoff.",
            "After the child returns, inspect its result and synthesize only claims supported by parent or child source evidence.",
          ].join("\n"),
        };
      } catch (err) {
        const error = err instanceof Error ? err.message : String(err);
        ctx.log("warn", "[parallel-research-gate] failed; failing open", {
          turnId: ctx.turnId,
          error,
        });
        return { action: "continue" };
      }
    },
  };
}
