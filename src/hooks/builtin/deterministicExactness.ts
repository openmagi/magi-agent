/**
 * LLM-backed exactness classifier.
 *
 * This intentionally does not route with local keyword/regex rules. The LLM
 * classifies whether the current user request needs deterministic runtime
 * evidence, then the runtime stores that decision as first-class contract
 * state so tools and beforeCommit gates can enforce it.
 */

import type {
  DeterministicRequirementKind,
} from "../../execution/ExecutionContract.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import type { HookContext, RegisteredHook } from "../types.js";
import { latestUserText } from "./classifyTurnMode.js";
import { getOrClassifyRequestMeta } from "./turnMetaClassifier.js";

export interface ExactnessClassifierOutput {
  requiresDeterministic: boolean;
  kinds: DeterministicRequirementKind[];
  reason: string;
  suggestedTools: string[];
  acceptanceCriteria: string[];
}

export interface ExactnessClassifierInput {
  llm: LLMClient;
  model: string;
  userMessage: string;
  timeoutMs?: number;
  signal?: AbortSignal;
}

const DEFAULT_TIMEOUT_MS = 8_000;
const VALID_KINDS: readonly DeterministicRequirementKind[] = [
  "clock",
  "date_range",
  "calculation",
  "counting",
  "data_query",
  "comparison",
];
const VALID_KIND_SET = new Set<string>(VALID_KINDS);

const CLASSIFIER_SYSTEM = [
  "You are a deterministic-execution classifier for an AI agent runtime.",
  "",
  "Decide whether the current user request requires deterministic runtime evidence before the assistant may answer.",
  "Require deterministic evidence for exact arithmetic, counts, dates, time windows, averages, sums, financial metrics, database/table analytics, comparisons, or any answer where guessing from language model intuition would be unsafe.",
  "Do not require deterministic evidence for conceptual explanations, broad summaries, opinion, brainstorming, or casual conversation unless exact numeric/date output is requested.",
  "",
  "Return ONLY strict JSON with this shape:",
  "{",
  '  "requiresDeterministic": boolean,',
  '  "kinds": ["clock" | "date_range" | "calculation" | "counting" | "data_query" | "comparison"],',
  '  "reason": string,',
  '  "suggestedTools": string[],',
  '  "acceptanceCriteria": string[]',
  "}",
  "",
  "Suggested tool names should use the native runtime tools when relevant: Clock, DateRange, Calculation, KnowledgeSearch, WebSearch, FileRead, Bash.",
  "When uncertain, choose requiresDeterministic=false.",
].join("\n");

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

function extractJsonObject(raw: string): unknown {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    const start = trimmed.indexOf("{");
    const end = trimmed.lastIndexOf("}");
    if (start === -1 || end <= start) return null;
    try {
      return JSON.parse(trimmed.slice(start, end + 1));
    } catch {
      return null;
    }
  }
}

export function parseExactnessClassifierOutput(raw: string): ExactnessClassifierOutput {
  const parsed = extractJsonObject(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return {
      requiresDeterministic: false,
      kinds: [],
      reason: "classifier output was not valid JSON",
      suggestedTools: [],
      acceptanceCriteria: [],
    };
  }

  const obj = parsed as Record<string, unknown>;
  const kinds = normalizeKinds(obj.kinds);
  const requiresDeterministic = obj.requiresDeterministic === true;
  return {
    requiresDeterministic,
    kinds: requiresDeterministic && kinds.length === 0 ? ["calculation"] : kinds,
    reason:
      typeof obj.reason === "string" && obj.reason.trim().length > 0
        ? obj.reason.trim().slice(0, 500)
        : requiresDeterministic
          ? "The request needs deterministic evidence."
          : "The request does not need deterministic evidence.",
    suggestedTools: normalizeStrings(obj.suggestedTools, 8),
    acceptanceCriteria: normalizeStrings(obj.acceptanceCriteria, 8),
  };
}

export async function classifyExactnessNeed(
  input: ExactnessClassifierInput,
): Promise<ExactnessClassifierOutput> {
  const deadline = Date.now() + (input.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const userPrompt = [
    "Classify this user request:",
    "",
    input.userMessage.slice(0, 4_000),
    "",
    "Return strict JSON only.",
  ].join("\n");

  let output = "";
  try {
    const stream = input.llm.stream({
      model: input.model,
      system: CLASSIFIER_SYSTEM,
      messages: [{ role: "user", content: [{ type: "text", text: userPrompt }] }],
      max_tokens: 700,
      temperature: 0,
      signal: input.signal,
    });
    for await (const event of stream) {
      if (Date.now() > deadline) break;
      if (event.kind === "text_delta") output += event.delta;
      if (event.kind === "message_end" || event.kind === "error") break;
    }
  } catch {
    return {
      requiresDeterministic: false,
      kinds: [],
      reason: "classifier failed open",
      suggestedTools: [],
      acceptanceCriteria: [],
    };
  }

  return parseExactnessClassifierOutput(output);
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_DETERMINISTIC_EXACTNESS;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export function makeDeterministicExactnessHook(): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:deterministic-exactness-classifier",
    point: "beforeLLMCall",
    priority: 6,
    blocking: true,
    failOpen: true,
    timeoutMs: DEFAULT_TIMEOUT_MS + 1_000,
    handler: async (args, ctx: HookContext) => {
      if (!isEnabled()) return { action: "continue" };
      if (args.iteration > 0) return { action: "continue" };
      const contract = ctx.executionContract;
      if (!contract) return { action: "continue" };

      const userMessage = latestUserText(args.messages);
      if (!userMessage) return { action: "continue" };

      const snapshot = contract.snapshot();
      const alreadyRecorded = snapshot.taskState.deterministicRequirements.some(
        (requirement) =>
          requirement.source === "llm_classifier" &&
          requirement.turnId === ctx.turnId,
      );
      if (alreadyRecorded) return { action: "continue" };

      const classified = await getOrClassifyRequestMeta(ctx, { userMessage });
      if (!classified.deterministic.requiresDeterministic) return { action: "continue" };

      const requirementId = `det_${ctx.turnId}_${
        snapshot.taskState.deterministicRequirements.length + 1
      }`;
      contract.recordDeterministicRequirement({
        requirementId,
        turnId: ctx.turnId,
        source: "llm_classifier",
        status: "active",
        kinds: classified.deterministic.kinds,
        reason: classified.deterministic.reason,
        suggestedTools: classified.deterministic.suggestedTools,
        acceptanceCriteria: classified.deterministic.acceptanceCriteria,
      });
      ctx.emit({
        type: "rule_check",
        ruleId: "deterministic-exactness-classifier",
        verdict: "violation",
        detail: `deterministic evidence required: ${classified.deterministic.kinds.join(", ")}`,
      });
      return { action: "continue" };
    },
  };
}
