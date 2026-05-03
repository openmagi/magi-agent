import { createHash } from "node:crypto";
import type {
  DeterministicRequirementKind,
  FinalAnswerMetaClassificationResult,
  RequestMetaClassificationResult,
} from "../../execution/ExecutionContract.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import type { HookContext } from "../types.js";

const REQUEST_CLASSIFIER_TIMEOUT_MS = 8_000;
const FINAL_CLASSIFIER_TIMEOUT_MS = 6_000;

const VALID_KINDS: readonly DeterministicRequirementKind[] = [
  "clock",
  "date_range",
  "calculation",
  "counting",
  "data_query",
  "comparison",
];
const VALID_KIND_SET = new Set<string>(VALID_KINDS);

const REQUEST_CLASSIFIER_SYSTEM = [
  "You are a runtime-control classifier for an AI agent.",
  "Classify the user's current request once so multiple hooks do not each call an LLM.",
  "Use semantic judgment across languages. Do not rely on keyword matching.",
  "",
  "Return ONLY strict JSON with this shape:",
  "{",
  '  "turnMode": {"label": "coding" | "exploratory" | "other", "confidence": number},',
  '  "skipTdd": boolean,',
  '  "implementationIntent": boolean,',
  '  "documentOrFileOperation": boolean,',
  '  "deterministic": {',
  '    "requiresDeterministic": boolean,',
  '    "kinds": ["clock" | "date_range" | "calculation" | "counting" | "data_query" | "comparison"],',
  '    "reason": string,',
  '    "suggestedTools": string[],',
  '    "acceptanceCriteria": string[]',
  "  },",
  '  "fileDelivery": {',
  '    "intent": "deliver_existing" | "none",',
  '    "path": string | null,',
  '    "wantsChatDelivery": boolean,',
  '    "wantsKbDelivery": boolean,',
  '    "wantsFileOutput": boolean',
  "  }",
  "}",
  "",
  "turnMode:",
  "- coding: author, fix, debug, refactor, test, review, or deploy code.",
  "- exploratory: prototype or throwaway experiment.",
  "- other: analysis, document writing, file delivery, research, chat, planning, or questions.",
  "",
  "skipTdd is true only when the user explicitly asks to skip tests/TDD.",
  "implementationIntent is true for non-trivial code implementation, not document/file creation.",
  "documentOrFileOperation is true for document/file/report/spreadsheet/export/create/convert/deliver requests.",
  "deterministic.requiresDeterministic is true for exact arithmetic, counts, dates, time windows, averages, financial metrics, database/table analytics, or exact comparisons.",
  "fileDelivery.intent is deliver_existing only when the user asks to send/attach/deliver an existing file, not to create, read, summarize, or analyze it.",
  "When uncertain, choose the safer non-triggering value.",
].join("\n");

const FINAL_ANSWER_CLASSIFIER_SYSTEM = [
  "You are a final-answer meta classifier for an AI agent runtime.",
  "Classify one drafted assistant answer once so multiple beforeCommit gates can share the result.",
  "Use semantic judgment across languages. Do not rely on keyword matching.",
  "",
  "Return ONLY strict JSON with this shape:",
  "{",
  '  "internalReasoningLeak": boolean,',
  '  "lazyRefusal": boolean,',
  '  "selfClaim": boolean,',
  '  "deferralPromise": boolean,',
  '  "assistantClaimsFileCreated": boolean,',
  '  "assistantClaimsChatDelivery": boolean,',
  '  "assistantClaimsKbDelivery": boolean,',
  '  "assistantReportsDeliveryFailure": boolean,',
  '  "reason": string',
  "}",
  "",
  "internalReasoningLeak: answer exposes hidden planning or meta-reasoning such as what the model should do next, rather than user-facing prose.",
  "lazyRefusal: answer claims it cannot find, access, verify, or provide something without evidence of actual investigation.",
  "selfClaim: answer asserts something about the bot's own workspace, prompt, configuration, or memory.",
  "deferralPromise: answer promises to deliver results later instead of completing or plainly failing in this turn.",
  "assistantClaimsFileCreated: answer says a file/document/report/artifact was created/generated/written/saved/prepared.",
  "assistantClaimsChatDelivery: answer says a file/result was sent, attached, uploaded, delivered, or made available in the current chat/channel.",
  "assistantClaimsKbDelivery: answer says something was saved/uploaded/added to KB, knowledge base, memory, or a persistent document store.",
  "assistantReportsDeliveryFailure: answer plainly says delivery failed, was not possible, was not sent, or asks the user for another path instead of claiming success.",
  "When uncertain, choose false.",
].join("\n");

export function hashMetaInput(...parts: readonly string[]): string {
  const hash = createHash("sha256");
  for (const part of parts) {
    hash.update(part);
    hash.update("\0");
  }
  return hash.digest("hex").slice(0, 24);
}

function normalizeStrings(value: unknown, limit: number): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter((item) => item.length > 0)
    .slice(0, limit);
}

function normalizeKinds(value: unknown): DeterministicRequirementKind[] {
  return normalizeStrings(value, VALID_KINDS.length).filter(
    (kind): kind is DeterministicRequirementKind => VALID_KIND_SET.has(kind),
  );
}

function extractJsonObject(raw: string): Record<string, unknown> | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    const start = trimmed.indexOf("{");
    const end = trimmed.lastIndexOf("}");
    if (start === -1 || end <= start) return null;
    try {
      const parsed = JSON.parse(trimmed.slice(start, end + 1)) as unknown;
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  }
}

function bool(value: unknown): boolean {
  return value === true;
}

function stringOrNull(value: unknown, max = 500): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed.slice(0, max) : null;
}

function objectField(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = obj[key];
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function clampConfidence(value: unknown): number {
  const n = typeof value === "number" && Number.isFinite(value) ? value : 0.5;
  return Math.max(0, Math.min(1, n));
}

export function defaultRequestMeta(reason = "classifier unavailable"): RequestMetaClassificationResult {
  return {
    turnMode: { label: "other", confidence: 0.5 },
    skipTdd: false,
    implementationIntent: false,
    documentOrFileOperation: false,
    deterministic: {
      requiresDeterministic: false,
      kinds: [],
      reason,
      suggestedTools: [],
      acceptanceCriteria: [],
    },
    fileDelivery: {
      intent: "none",
      path: null,
      wantsChatDelivery: false,
      wantsKbDelivery: false,
      wantsFileOutput: false,
    },
  };
}

export function parseRequestMetaOutput(raw: string): RequestMetaClassificationResult {
  const parsed = extractJsonObject(raw);
  if (!parsed) return defaultRequestMeta("classifier output was not valid JSON");

  const mode = objectField(parsed, "turnMode");
  const rawLabel = stringOrNull(mode.label, 30);
  const label =
    rawLabel === "coding" || rawLabel === "exploratory" || rawLabel === "other"
      ? rawLabel
      : "other";
  const deterministic = objectField(parsed, "deterministic");
  const kinds = normalizeKinds(deterministic.kinds);
  const requiresDeterministic = bool(deterministic.requiresDeterministic);
  const fileDelivery = objectField(parsed, "fileDelivery");
  const rawIntent = stringOrNull(fileDelivery.intent, 30);
  const intent = rawIntent === "deliver_existing" ? "deliver_existing" : "none";

  return {
    turnMode: {
      label,
      confidence: clampConfidence(mode.confidence),
    },
    skipTdd: bool(parsed.skipTdd),
    implementationIntent: bool(parsed.implementationIntent),
    documentOrFileOperation: bool(parsed.documentOrFileOperation),
    deterministic: {
      requiresDeterministic,
      kinds: requiresDeterministic && kinds.length === 0 ? ["calculation"] : kinds,
      reason:
        stringOrNull(deterministic.reason) ??
        (requiresDeterministic
          ? "The request needs deterministic evidence."
          : "The request does not need deterministic evidence."),
      suggestedTools: normalizeStrings(deterministic.suggestedTools, 8),
      acceptanceCriteria: normalizeStrings(deterministic.acceptanceCriteria, 8),
    },
    fileDelivery: {
      intent,
      path: stringOrNull(fileDelivery.path, 1_000),
      wantsChatDelivery: bool(fileDelivery.wantsChatDelivery),
      wantsKbDelivery: bool(fileDelivery.wantsKbDelivery),
      wantsFileOutput: bool(fileDelivery.wantsFileOutput),
    },
  };
}

export function defaultFinalAnswerMeta(
  reason = "classifier unavailable",
): FinalAnswerMetaClassificationResult {
  return {
    internalReasoningLeak: false,
    lazyRefusal: false,
    selfClaim: false,
    deferralPromise: false,
    assistantClaimsFileCreated: false,
    assistantClaimsChatDelivery: false,
    assistantClaimsKbDelivery: false,
    assistantReportsDeliveryFailure: false,
    reason,
  };
}

export function parseFinalAnswerMetaOutput(raw: string): FinalAnswerMetaClassificationResult {
  const parsed = extractJsonObject(raw);
  if (!parsed) return defaultFinalAnswerMeta("classifier output was not valid JSON");
  return {
    internalReasoningLeak: bool(parsed.internalReasoningLeak),
    lazyRefusal: bool(parsed.lazyRefusal),
    selfClaim: bool(parsed.selfClaim),
    deferralPromise: bool(parsed.deferralPromise),
    assistantClaimsFileCreated: bool(parsed.assistantClaimsFileCreated),
    assistantClaimsChatDelivery: bool(parsed.assistantClaimsChatDelivery),
    assistantClaimsKbDelivery: bool(parsed.assistantClaimsKbDelivery),
    assistantReportsDeliveryFailure: bool(parsed.assistantReportsDeliveryFailure),
    reason: stringOrNull(parsed.reason) ?? "classified final answer metadata.",
  };
}

async function streamClassifier(input: {
  llm: LLMClient;
  model: string;
  system: string;
  userText: string;
  maxTokens: number;
  timeoutMs: number;
  signal?: AbortSignal;
}): Promise<string> {
  const deadline = Date.now() + input.timeoutMs;
  let output = "";
  const stream = input.llm.stream({
    model: input.model,
    system: input.system,
    messages: [{ role: "user", content: [{ type: "text", text: input.userText }] }],
    max_tokens: input.maxTokens,
    temperature: 0,
    signal: input.signal,
  });
  for await (const event of stream) {
    if (Date.now() > deadline) break;
    if (event.kind === "text_delta") output += event.delta;
    if (event.kind === "message_end" || event.kind === "error") break;
  }
  return output;
}

export async function classifyRequestMeta(input: {
  llm: LLMClient;
  model: string;
  userMessage: string;
  timeoutMs?: number;
  signal?: AbortSignal;
}): Promise<RequestMetaClassificationResult> {
  if (!input.userMessage.trim()) return defaultRequestMeta("empty request");
  try {
    const raw = await streamClassifier({
      llm: input.llm,
      model: input.model,
      system: REQUEST_CLASSIFIER_SYSTEM,
      userText: [
        "Classify this user request:",
        "",
        input.userMessage.slice(0, 4_000),
        "",
        "Return strict JSON only.",
      ].join("\n"),
      maxTokens: 900,
      timeoutMs: input.timeoutMs ?? REQUEST_CLASSIFIER_TIMEOUT_MS,
      signal: input.signal,
    });
    return parseRequestMetaOutput(raw);
  } catch {
    return defaultRequestMeta("classifier failed open");
  }
}

export async function classifyFinalAnswerMeta(input: {
  llm: LLMClient;
  model: string;
  userMessage: string;
  assistantText: string;
  timeoutMs?: number;
  signal?: AbortSignal;
}): Promise<FinalAnswerMetaClassificationResult> {
  if (!input.assistantText.trim()) return defaultFinalAnswerMeta("empty final answer");
  try {
    const raw = await streamClassifier({
      llm: input.llm,
      model: input.model,
      system: FINAL_ANSWER_CLASSIFIER_SYSTEM,
      userText: [
        "User request:",
        input.userMessage.slice(0, 2_000),
        "",
        "Draft assistant answer:",
        input.assistantText.slice(0, 4_000),
        "",
        "Return strict JSON only.",
      ].join("\n"),
      maxTokens: 700,
      timeoutMs: input.timeoutMs ?? FINAL_CLASSIFIER_TIMEOUT_MS,
      signal: input.signal,
    });
    return parseFinalAnswerMetaOutput(raw);
  } catch {
    return defaultFinalAnswerMeta("classifier failed open");
  }
}

export async function getOrClassifyRequestMeta(
  ctx: HookContext,
  input: { userMessage: string },
): Promise<RequestMetaClassificationResult> {
  const inputHash = hashMetaInput(input.userMessage);
  const cached = ctx.executionContract?.getRequestMetaClassification(ctx.turnId, inputHash);
  if (cached) return cached;
  if (!ctx.llm) return defaultRequestMeta("no LLM context");

  const result = await classifyRequestMeta({
    llm: ctx.llm,
    model: ctx.agentModel,
    userMessage: input.userMessage,
    timeoutMs: Math.min(REQUEST_CLASSIFIER_TIMEOUT_MS, ctx.deadlineMs),
    signal: ctx.abortSignal,
  });
  ctx.executionContract?.recordRequestMetaClassification({
    turnId: ctx.turnId,
    inputHash,
    source: "llm_classifier",
    result,
  });
  return result;
}

export async function getOrClassifyFinalAnswerMeta(
  ctx: HookContext,
  input: { userMessage: string; assistantText: string },
): Promise<FinalAnswerMetaClassificationResult> {
  const inputHash = hashMetaInput(input.userMessage, input.assistantText);
  const cached = ctx.executionContract?.getFinalAnswerClassification(ctx.turnId, inputHash);
  if (cached) return cached;
  if (!ctx.llm) return defaultFinalAnswerMeta("no LLM context");

  const result = await classifyFinalAnswerMeta({
    llm: ctx.llm,
    model: ctx.agentModel,
    userMessage: input.userMessage,
    assistantText: input.assistantText,
    timeoutMs: Math.min(FINAL_CLASSIFIER_TIMEOUT_MS, ctx.deadlineMs),
    signal: ctx.abortSignal,
  });
  ctx.executionContract?.recordFinalAnswerClassification({
    turnId: ctx.turnId,
    inputHash,
    source: "llm_classifier",
    result,
  });
  return result;
}
