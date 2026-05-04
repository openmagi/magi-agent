export type MemoryContinuity = "active" | "related" | "background";
export type MemoryRecallSource = "qmd" | "root";

export interface MemoryRecallRecord {
  turnId: string;
  source: MemoryRecallSource;
  path: string;
  continuity: MemoryContinuity;
  distinctivePhrases: string[];
  recordedAt?: number;
}

export interface ClassifyMemoryContinuityInput {
  latestUserText: string;
  memoryText: string;
  source: MemoryRecallSource;
}

export interface StaleMemoryPromotionInput {
  latestUserText: string;
  assistantText: string;
  records: readonly MemoryRecallRecord[];
}

export interface StaleMemoryPromotionResult {
  retry: boolean;
  phrase?: string;
  path?: string;
  reason?: string;
}

const CONTINUATION_CUE_RE =
  /\b(?:continue|resume|again|earlier|previous|that issue|that topic|the one we discussed)\b|(?:아까|전에|이어서|다시|그거|그 문제|그 선택|그 주제|저번|이전)/iu;
const DECISION_REQUEST_RE =
  /[?？]\s*$|(?:어떻게\s*할까요|할까요|정할까요|고를까요|선택(?:할|해야|해)|결정(?:할|해야|해)|확인(?:해|할)|choose|decide|confirm|which)/iu;

const STOP_TOKENS = new Set([
  "the",
  "and",
  "for",
  "with",
  "that",
  "this",
  "from",
  "about",
  "current",
  "project",
  "memory",
  "context",
  "active",
  "summary",
  "이",
  "그",
  "저",
  "것",
  "수",
  "등",
  "및",
  "그리고",
  "하지만",
  "현재",
  "프로젝트",
  "맥락",
]);

export function hasContinuationCue(text: string): boolean {
  return CONTINUATION_CUE_RE.test(text);
}

export function classifyMemoryContinuity(
  input: ClassifyMemoryContinuityInput,
): MemoryContinuity {
  const latestTokens = significantTokens(input.latestUserText);
  const memoryTokens = significantTokens(input.memoryText);
  const overlap = overlapCount(latestTokens, memoryTokens);

  if (input.source === "root" && !hasContinuationCue(input.latestUserText)) {
    return "background";
  }

  if (hasContinuationCue(input.latestUserText) && overlap > 0) {
    return "active";
  }

  return overlap > 0 ? "related" : "background";
}

export function extractDistinctivePhrases(text: string): string[] {
  const tokens = tokenize(text)
    .map((token) => normalizeToken(token))
    .filter((token) => token.length >= 2 && !STOP_TOKENS.has(token));
  const phrases: string[] = [];
  const seen = new Set<string>();

  for (let size = Math.min(5, tokens.length); size >= 2; size -= 1) {
    for (let i = 0; i + size <= tokens.length; i += 1) {
      const phrase = tokens.slice(i, i + size).join(" ");
      const normalized = normalizeText(phrase);
      if (normalized.length < 6 || seen.has(normalized)) continue;
      seen.add(normalized);
      phrases.push(phrase);
      if (phrases.length >= 12) return phrases;
    }
  }

  return phrases;
}

export function shouldRetryStaleMemoryPromotion(
  input: StaleMemoryPromotionInput,
): StaleMemoryPromotionResult {
  if (hasContinuationCue(input.latestUserText)) return { retry: false };
  if (!introducesDecisionRequest(input.assistantText)) return { retry: false };

  const latest = normalizeText(input.latestUserText);
  const assistant = normalizeText(input.assistantText);

  for (const record of input.records) {
    if (record.continuity !== "background") continue;
    for (const phrase of record.distinctivePhrases) {
      const normalizedPhrase = normalizeText(phrase);
      if (normalizedPhrase.length < 6) continue;
      if (!assistant.includes(normalizedPhrase)) continue;
      if (latest.includes(normalizedPhrase)) continue;
      return {
        retry: true,
        phrase,
        path: record.path,
        reason: "background memory phrase promoted into decision request",
      };
    }
  }

  return { retry: false };
}

function introducesDecisionRequest(text: string): boolean {
  return DECISION_REQUEST_RE.test(text.trim());
}

function overlapCount(left: readonly string[], right: readonly string[]): number {
  const rightSet = new Set(right);
  let count = 0;
  for (const token of new Set(left)) {
    if (rightSet.has(token)) count += 1;
  }
  return count;
}

function significantTokens(text: string): string[] {
  return tokenize(text)
    .map((token) => normalizeToken(token))
    .filter((token) => token.length >= 2 && !STOP_TOKENS.has(token));
}

function tokenize(text: string): string[] {
  return text.normalize("NFC").match(/[\p{L}\p{N}]+/gu) ?? [];
}

function normalizeToken(token: string): string {
  const lowered = token.normalize("NFC").toLowerCase();
  return lowered.replace(/(으로|에서|에게|께|을|를|은|는|이|가|과|와|도|만|로|에|의)$/u, "");
}

function normalizeText(text: string): string {
  return tokenize(text)
    .map((token) => normalizeToken(token))
    .join(" ");
}
