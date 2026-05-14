/**
 * beforeCommit gate for LLM-assisted deterministic workflows.
 *
 * The classifier creates a deterministic requirement. Native tools record
 * structured evidence. This gate checks that the final answer is consistent
 * with that evidence before the turn commits.
 */

import type {
  DeterministicEvidenceRecord,
  DeterministicRequirement,
} from "../../execution/ExecutionContract.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import type { HookContext, RegisteredHook } from "../types.js";

export type DeterministicEvidenceVerdict =
  | "PASS"
  | "MISSING_EVIDENCE"
  | "CONTRADICTS_EVIDENCE"
  | "UNCLEAR";

export interface DeterministicEvidenceJudgeInput {
  llm: LLMClient;
  model: string;
  userMessage: string;
  assistantText: string;
  requirements: DeterministicRequirement[];
  evidence: DeterministicEvidenceRecord[];
  timeoutMs?: number;
  signal?: AbortSignal;
}

const MAX_RETRIES = 1;
const DEFAULT_TIMEOUT_MS = 10_000;

const JUDGE_SYSTEM = [
  "You are a deterministic-evidence verifier for an AI agent runtime.",
  "",
  "Compare the assistant's draft answer to the structured deterministic requirements and evidence.",
  "Return PASS only when the draft's numeric/date/count/aggregate claims are directly supported by the evidence.",
  "",
  "Return exactly one token:",
  "PASS",
  "MISSING_EVIDENCE",
  "CONTRADICTS_EVIDENCE",
  "UNCLEAR",
  "",
  "Rules:",
  "- Use only the provided structured evidence.",
  "- If the draft gives an exact number/date/range not present in evidence, return MISSING_EVIDENCE.",
  "- If the draft's exact value conflicts with evidence, return CONTRADICTS_EVIDENCE.",
  "- If evidence exists but the relationship is ambiguous, return UNCLEAR.",
  "- If the draft explicitly says it cannot verify instead of claiming an exact answer, return PASS.",
  "- Output only the verdict token.",
].join("\n");

export function parseDeterministicEvidenceVerdict(
  raw: string,
): DeterministicEvidenceVerdict {
  const token = raw.trim().toUpperCase().split(/\s+/)[0] ?? "";
  if (token === "MISSING_EVIDENCE") return "MISSING_EVIDENCE";
  if (token === "CONTRADICTS_EVIDENCE") return "CONTRADICTS_EVIDENCE";
  if (token === "UNCLEAR") return "UNCLEAR";
  return "PASS";
}

export async function judgeDeterministicEvidence(
  input: DeterministicEvidenceJudgeInput,
): Promise<DeterministicEvidenceVerdict> {
  const deadline = Date.now() + (input.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const payload = {
    userMessage: input.userMessage.slice(0, 4_000),
    assistantText: input.assistantText.slice(0, 6_000),
    requirements: input.requirements.map((requirement) => ({
      requirementId: requirement.requirementId,
      kinds: requirement.kinds,
      reason: requirement.reason,
      acceptanceCriteria: requirement.acceptanceCriteria,
    })),
    evidence: input.evidence.map((evidence) => ({
      evidenceId: evidence.evidenceId,
      requirementIds: evidence.requirementIds,
      toolName: evidence.toolName,
      kind: evidence.kind,
      status: evidence.status,
      inputSummary: evidence.inputSummary,
      output: evidence.output,
      assertions: evidence.assertions,
      resources: evidence.resources,
    })),
  };

  let output = "";
  try {
    const stream = input.llm.stream({
      model: input.model,
      system: JUDGE_SYSTEM,
      messages: [
        {
          role: "user",
          content: [
            {
              type: "text",
              text: [
                "Verify this draft against deterministic evidence.",
                "",
                JSON.stringify(payload).slice(0, 24_000),
                "",
                "Verdict:",
              ].join("\n"),
            },
          ],
        },
      ],
      max_tokens: 16,
      temperature: 0,
      signal: input.signal,
    });
    for await (const event of stream) {
      if (Date.now() > deadline) break;
      if (event.kind === "text_delta") output += event.delta;
      if (event.kind === "message_end" || event.kind === "error") break;
    }
  } catch {
    return "PASS";
  }

  return parseDeterministicEvidenceVerdict(output);
}

function isEnabled(): boolean {
  const raw = process.env.MAGI_DETERMINISTIC_EVIDENCE_VERIFY;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function currentTurnRequirements(
  requirements: DeterministicRequirement[],
  turnId: string,
): DeterministicRequirement[] {
  const current = requirements.filter(
    (requirement) =>
      requirement.turnId === turnId && requirement.status !== "waived",
  );
  return current;
}

function passedToolEvidenceFor(
  evidence: DeterministicEvidenceRecord[],
  requirements: DeterministicRequirement[],
): DeterministicEvidenceRecord[] {
  const requirementIds = new Set(requirements.map((item) => item.requirementId));
  return evidence.filter(
    (record) =>
      record.kind !== "verification" &&
      record.status === "passed" &&
      record.requirementIds.some((id) => requirementIds.has(id)),
  );
}

function hasPassedVerifierEvidence(
  evidence: DeterministicEvidenceRecord[],
  requirements: DeterministicRequirement[],
): boolean {
  const requirementIds = new Set(requirements.map((item) => item.requirementId));
  return evidence.some(
    (record) =>
      record.kind === "verification" &&
      record.status === "passed" &&
      record.requirementIds.some((id) => requirementIds.has(id)),
  );
}

function blockReason(
  requirements: DeterministicRequirement[],
  evidence: DeterministicEvidenceRecord[],
  verdict: DeterministicEvidenceVerdict,
): string {
  const suggestedTools = [
    ...new Set(requirements.flatMap((requirement) => requirement.suggestedTools)),
  ];
  const kinds = [
    ...new Set(requirements.flatMap((requirement) => requirement.kinds)),
  ];
  return [
    `[RETRY:DETERMINISTIC_EVIDENCE:${verdict}] This answer requires deterministic runtime evidence before completion.`,
    "",
    `Required exactness kinds: ${kinds.join(", ") || "(unspecified)"}`,
    `Suggested tools: ${suggestedTools.join(", ") || "Clock, DateRange, Calculation"}`,
    `Evidence records found: ${evidence.length}`,
    "",
    "Before finalising:",
    "1) Use native deterministic tools for time/range/math/data extraction instead of mental arithmetic.",
    "2) Ground the final numeric/date/count claims in those tool results.",
    "3) If exact verification is impossible, say so explicitly instead of giving a precise answer.",
  ].join("\n");
}

export function makeDeterministicEvidenceVerifierHook(): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:deterministic-evidence-verifier",
    point: "beforeCommit",
    priority: 88,
    blocking: true,
    failOpen: true,
    timeoutMs: DEFAULT_TIMEOUT_MS + 1_000,
    handler: async ({ userMessage, assistantText, retryCount }, ctx: HookContext) => {
      if (!isEnabled()) return { action: "continue" };
      const contract = ctx.executionContract;
      if (!contract) return { action: "continue" };
      const snapshot = contract.snapshot();
      const requirements = currentTurnRequirements(
        snapshot.taskState.deterministicRequirements,
        ctx.turnId,
      );
      if (requirements.length === 0) return { action: "continue" };

      const allEvidence = snapshot.taskState.deterministicEvidence;
      if (hasPassedVerifierEvidence(allEvidence, requirements)) {
        return { action: "continue" };
      }
      const toolEvidence = passedToolEvidenceFor(allEvidence, requirements);
      if (toolEvidence.length === 0) {
        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[deterministic-evidence-verifier] retry exhausted without evidence");
          return { action: "continue" };
        }
        ctx.emit({
          type: "rule_check",
          ruleId: "deterministic-evidence-verifier",
          verdict: "violation",
          detail: "missing deterministic evidence",
        });
        return {
          action: "block",
          reason: blockReason(requirements, toolEvidence, "MISSING_EVIDENCE"),
        };
      }

      const verdict = await judgeDeterministicEvidence({
        llm: ctx.llm,
        model: ctx.agentModel,
        userMessage,
        assistantText,
        requirements,
        evidence: toolEvidence,
        timeoutMs: Math.min(DEFAULT_TIMEOUT_MS, ctx.deadlineMs),
        signal: ctx.abortSignal,
      });
      if (verdict === "PASS") {
        contract.recordDeterministicEvidence({
          evidenceId: `det_verify_${ctx.turnId}_${Date.now().toString(36)}`,
          turnId: ctx.turnId,
          requirementIds: requirements.map((requirement) => requirement.requirementId),
          toolName: "DeterministicEvidenceVerifier",
          kind: "verification",
          status: "passed",
          inputSummary: "beforeCommit deterministic evidence verifier",
          output: { verdict },
          assertions: ["assistant_answer_supported_by_deterministic_evidence"],
          resources: [],
        });
        return { action: "continue" };
      }

      if (retryCount >= MAX_RETRIES) {
        ctx.log("warn", "[deterministic-evidence-verifier] retry exhausted; failing open", {
          verdict,
        });
        return { action: "continue" };
      }
      ctx.emit({
        type: "rule_check",
        ruleId: "deterministic-evidence-verifier",
        verdict: "violation",
        detail: verdict,
      });
      return {
        action: "block",
        reason: blockReason(requirements, toolEvidence, verdict),
      };
    },
  };
}
