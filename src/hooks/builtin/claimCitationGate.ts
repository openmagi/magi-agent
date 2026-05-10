import type { SourceLedgerRecord } from "../../research/SourceLedger.js";
import type { RegisteredHook, HookContext } from "../types.js";

const MAX_RETRIES = 1;
const LONG_SOURCED_DRAFT_MIN_CHARS = 4_000;
const LONG_SOURCED_DRAFT_MIN_SOURCES = 3;
const LONG_SOURCED_DRAFT_MAX_MISSING = 3;

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

interface CitationCoverageMissing {
  text: string;
}

interface CitationCoverage {
  text: string;
  status: "uncertain" | "covered" | "missing";
  sourceIds: string[];
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

function truncate(value: string, maxLength: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, Math.max(0, maxLength - 3)).trimEnd()}...`;
}

function renderSourceRepairContext(
  sources: readonly SourceLedgerRecord[],
  missing: readonly CitationCoverageMissing[],
): string[] {
  const lines = [
    "Available inspected sources:",
    ...sources.slice(0, 8).map((source) => {
      const title = source.title ? ` - ${truncate(source.title, 80)}` : "";
      const snippet = source.snippets?.[0]
        ? ` | excerpt: ${truncate(source.snippets[0], 180)}`
        : "";
      return `- [${source.sourceId}] ${source.kind}${title}: ${source.uri}${snippet}`;
    }),
  ];
  if (sources.length > 8) {
    lines.push(`- ${sources.length - 8} more inspected sources omitted from retry prompt.`);
  }
  lines.push(
    "Missing citation examples:",
    ...missing.slice(0, 6).map((claim, index) => {
      return `${index + 1}. ${truncate(claim.text, 220)}`;
    }),
  );
  if (missing.length > 6) {
    lines.push(`${missing.length - 6} more uncited claims omitted from retry prompt.`);
  }
  return lines;
}

function shouldFailOpenLongSourcedDraft(
  assistantText: string,
  sources: readonly SourceLedgerRecord[],
  coverage: readonly CitationCoverage[],
): boolean {
  if (assistantText.length < LONG_SOURCED_DRAFT_MIN_CHARS) return false;
  if (sources.length < LONG_SOURCED_DRAFT_MIN_SOURCES) return false;

  const covered = coverage.filter((claim) => claim.status === "covered").length;
  if (covered === 0) return false;

  const missing = coverage.filter((claim) => claim.status === "missing").length;
  return missing > 0 && missing <= LONG_SOURCED_DRAFT_MAX_MISSING;
}

export function makeClaimCitationGateHook(): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:claim-citation-gate",
    point: "beforeCommit",
    priority: 81,
    blocking: true,
    failOpen: false,
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

        const coverage: CitationCoverage[] = claims.map((claim) => {
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

        if (shouldFailOpenLongSourcedDraft(assistantText, sources, coverage)) {
          ctx.log(
            "warn",
            "[claim-citation-gate] long sourced draft has partial citation gaps; failing open",
            {
              missing: missing.length,
              sources: sources.length,
              claims: coverage.length,
            },
          );
          return { action: "continue" };
        }

        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[claim-citation-gate] retry exhausted; failing closed", {
            missing: missing.length,
          });
          return {
            action: "block",
            reason: [
              "[RULE:CLAIM_CITATION_REQUIRED]",
              "Research claims still lack inspected-source citations after verifier retry.",
              `Missing citation count: ${missing.length}.`,
              "No answer should be committed until each concrete claim cites an inspected source id/URL, or unsupported claims are removed/downgraded as uncertain.",
            ].join("\n"),
          };
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
            ...renderSourceRepairContext(sources, missing),
            "Regenerate with each concrete claim citing an inspected source id like [src_1] or the inspected URL.",
            "Use only source ids from the inspected source list above; do not invent citations.",
            "If a claim is not supported by these sources, remove it or mark it uncertain.",
          ].join("\n"),
        };
      } catch (err) {
        const error = err instanceof Error ? err.message : String(err);
        ctx.log("warn", "[claim-citation-gate] failed; failing closed", {
          error,
        });
        return {
          action: "block",
          reason: [
            "[RULE:CLAIM_CITATION_GATE_ERROR]",
            "Claim-citation verifier failed while checking citation coverage.",
            `Verifier error: ${truncate(error, 240)}.`,
            "No answer should be committed until the verifier can confirm citation coverage.",
          ].join("\n"),
        };
      }
    },
  };
}
