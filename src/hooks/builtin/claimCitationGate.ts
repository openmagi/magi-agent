import type { SourceLedgerRecord } from "../../research/SourceLedger.js";
import type { RegisteredHook, HookContext } from "../types.js";

const MAX_RETRIES = 1;

const UNCERTAIN_RE =
  /\b(?:may|might|could|appears|seems|probably|unconfirmed|manual confirmation|needs confirmation)\b|(?:확인 필요|불확실|추정|가능성|확인되지|원문 확인 불가|수동 확인)/i;
const PROCEDURAL_RE =
  /^(?:다음|먼저|이제|요약|정리|출처|참고|sources?|references?)\b|^(?:I will|I'll|I can|Let me)\b/i;
const SOURCE_ID_RE = /\bsrc_\d+\b/g;
const FACTUAL_VERB_RE =
  /\b(?:is|are|has|have|supports?|provides?|uses?|contains?|includes?|released?|changed?|defaults?)\b|(?:이다|입니다|있다|있습니다|지원|제공|사용|포함|변경|출시|기본값|구성|도구)/i;

interface CitationClaim {
  text: string;
  uncertain: boolean;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_CLAIM_CITATION_GATE;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function splitCandidateClaims(assistantText: string): string[] {
  const candidates: string[] = [];
  for (const rawLine of assistantText.split(/\n+/)) {
    const line = rawLine
      .replace(/^\s*(?:[-*]|\d+[.)])\s+/, "")
      .trim();
    if (!line) continue;
    candidates.push(
      ...line
        .split(/(?<=[.!?])\s+/)
        .map((part) => part.trim())
        .filter(Boolean),
    );
  }
  return candidates;
}

export function extractCitationClaims(assistantText: string): CitationClaim[] {
  return splitCandidateClaims(assistantText).flatMap((text) => {
    if (text.length < 16) return [];
    if (PROCEDURAL_RE.test(text)) return [];
    if (!FACTUAL_VERB_RE.test(text)) return [];
    return [{ text, uncertain: UNCERTAIN_RE.test(text) }];
  });
}

function hostFor(uri: string): string | null {
  try {
    return new URL(uri).hostname.toLowerCase();
  } catch {
    return null;
  }
}

function citedSourceIds(text: string, sources: readonly SourceLedgerRecord[]): string[] {
  const explicitIds = new Set(text.match(SOURCE_ID_RE) ?? []);
  const lowered = text.toLowerCase();
  const sourceIds = new Set<string>();
  for (const source of sources) {
    if (explicitIds.has(source.sourceId)) {
      sourceIds.add(source.sourceId);
      continue;
    }
    const uri = source.uri.toLowerCase();
    const host = hostFor(source.uri);
    if (uri.startsWith("http") && lowered.includes(uri)) {
      sourceIds.add(source.sourceId);
      continue;
    }
    if (host && lowered.includes(host)) {
      sourceIds.add(source.sourceId);
    }
  }
  return [...sourceIds];
}

function sourceSensitiveTurn(ctx: HookContext, userMessage: string): boolean {
  const existing = ctx.researchContract?.turnFor(ctx.turnId);
  if (existing) return existing.sourceSensitive;
  return ctx.researchContract?.startTurn({
    turnId: ctx.turnId,
    userMessage,
  }).sourceSensitive ?? false;
}

export function makeClaimCitationGateHook(): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:claim-citation-gate",
    point: "beforeCommit",
    priority: 81,
    blocking: true,
    failOpen: true,
    timeoutMs: 1_000,
    handler: async ({ assistantText, userMessage, retryCount }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!assistantText.trim()) return { action: "continue" };

        const sources = ctx.sourceLedger?.sourcesForTurn(ctx.turnId) ?? [];
        const sensitive = sourceSensitiveTurn(ctx, userMessage) || sources.length > 0;
        if (!sensitive) return { action: "continue" };

        const claims = extractCitationClaims(assistantText);
        if (claims.length === 0) return { action: "continue" };

        const coverage = claims.map((claim) => {
          const sourceIds = claim.uncertain ? [] : citedSourceIds(claim.text, sources);
          return {
            text: claim.text,
            status: claim.uncertain ? "uncertain" as const : sourceIds.length > 0 ? "covered" as const : "missing" as const,
            sourceIds,
          };
        });
        ctx.researchContract?.recordCitationCoverage(ctx.turnId, coverage);

        const missing = coverage.filter((claim) => claim.status === "missing");
        if (missing.length === 0) {
          ctx.emit({
            type: "rule_check",
            ruleId: "claim-citation-gate",
            verdict: "ok",
            detail: `${coverage.length} claims checked`,
          });
          return { action: "continue" };
        }

        ctx.emit({
          type: "rule_check",
          ruleId: "claim-citation-gate",
          verdict: "violation",
          detail: `${missing.length} uncited claims`,
        });

        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[claim-citation-gate] retry exhausted; failing open", {
            missing: missing.length,
          });
          return { action: "continue" };
        }

        if (sources.length === 0) {
          return {
            action: "block",
            reason: [
              "[RETRY:CLAIM_CITATION]",
              "This is a source-sensitive research answer, but no source was inspected this turn.",
              "Use WebSearch and then WebFetch to inspect primary/current sources before making factual claims.",
              "If sources cannot be inspected, explicitly downgrade claims as uncertain.",
            ].join("\n"),
          };
        }

        return {
          action: "block",
          reason: [
            "[RETRY:CLAIM_CITATION]",
            "Your draft contains factual research claims without per-claim citations.",
            "Regenerate with each concrete claim citing an inspected source id like [src_1] or the inspected URL.",
            "Unsupported claims must be removed or explicitly framed as uncertain.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log("warn", "[claim-citation-gate] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}
