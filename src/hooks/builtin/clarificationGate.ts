/**
 * Clarification gate — pauses non-trivial ambiguous work before the
 * main LLM starts acting.
 *
 * Uses the shared request-meta classifier, so this does not add a new
 * LLM call. The gate only asks when the classifier says clarification
 * is needed and the rest of the same meta payload shows concrete work.
 */

import type { ControlRequestRecord } from "../../control/ControlEvents.js";
import type { RequestMetaClassificationResult } from "../../execution/ExecutionContract.js";
import type { LLMMessage } from "../../transport/LLMClient.js";
import type { HookContext, RegisteredHook } from "../types.js";
import { latestUserText } from "./classifyTurnMode.js";
import { getOrClassifyRequestMeta } from "./turnMetaClassifier.js";

const CLARIFICATION_REQUEST_TIMEOUT_MS = 5 * 60_000;
const CLARIFICATION_HOOK_TIMEOUT_MS = CLARIFICATION_REQUEST_TIMEOUT_MS + 10_000;

export interface AskClarificationInput {
  sessionKey: string;
  turnId: string;
  question: string;
  choices: string[];
  allowFreeText: boolean;
  reason: string;
  riskIfAssumed: string;
  signal?: AbortSignal;
  onRequest?: (request: ControlRequestRecord) => void;
}

export interface AskClarificationResult {
  request: ControlRequestRecord;
  resolved: ControlRequestRecord;
}

export interface ClarificationGateAgent {
  askClarification(input: AskClarificationInput): Promise<AskClarificationResult>;
}

export interface ClarificationGateOptions {
  agent: ClarificationGateAgent;
}

export function isClarificationGateEnabled(env: string | undefined): boolean {
  const v = (env ?? "on").trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export function shouldRequestClarification(
  meta: RequestMetaClassificationResult,
): boolean {
  const clarification = meta.clarification;
  if (!clarification?.needed) return false;
  if (!clarification.question?.trim()) return false;

  const isConcreteWork =
    meta.goalProgress.requiresAction ||
    meta.implementationIntent ||
    meta.documentOrFileOperation ||
    meta.deterministic.requiresDeterministic ||
    meta.fileDelivery.intent === "deliver_existing" ||
    meta.fileDelivery.wantsChatDelivery ||
    meta.fileDelivery.wantsKbDelivery ||
    meta.fileDelivery.wantsFileOutput ||
    meta.planning.need !== "none";

  return isConcreteWork;
}

function choiceLabelForAnswer(
  answer: string,
  request: ControlRequestRecord,
): string {
  const proposed = request.proposedInput;
  if (!proposed || typeof proposed !== "object") return answer;
  const choices = (proposed as { choices?: unknown }).choices;
  if (!Array.isArray(choices)) return answer;
  for (const choice of choices) {
    if (!choice || typeof choice !== "object") continue;
    const candidate = choice as { id?: unknown; label?: unknown };
    if (candidate.id === answer && typeof candidate.label === "string") {
      return candidate.label;
    }
  }
  return answer;
}

function renderClarificationResponse(
  request: ControlRequestRecord,
  resolved: ControlRequestRecord,
): string | null {
  const answer = resolved.answer?.trim();
  if (!answer) return null;
  const label = choiceLabelForAnswer(answer, request);
  return [
    "<clarification_response>",
    `Question: ${request.prompt}`,
    `Answer: ${label}`,
    label === answer ? "" : `Answer choice id: ${answer}`,
    "</clarification_response>",
    "Use this clarification as the authoritative user answer for the current turn.",
  ]
    .filter((line) => line.length > 0)
    .join("\n");
}

function appendClarificationMessage(
  messages: LLMMessage[],
  clarification: string,
): LLMMessage[] {
  return [
    ...messages,
    {
      role: "user",
      content: clarification,
    },
  ];
}

export function makeClarificationGateHook(
  opts: ClarificationGateOptions,
): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:clarification-gate",
    point: "beforeLLMCall",
    priority: 4,
    blocking: true,
    timeoutMs: CLARIFICATION_HOOK_TIMEOUT_MS,
    handler: async (args, ctx: HookContext) => {
      if (!isClarificationGateEnabled(process.env.CORE_AGENT_CLARIFICATION_GATE)) {
        return { action: "continue" };
      }
      if (args.iteration > 0) return { action: "continue" };

      const userMessage = latestUserText(args.messages);
      if (!userMessage) return { action: "continue" };

      const meta = await getOrClassifyRequestMeta(ctx, { userMessage });
      if (!shouldRequestClarification(meta)) return { action: "continue" };

      const clarificationMeta = meta.clarification;
      const question = clarificationMeta.question?.trim();
      if (!question) return { action: "continue" };

      const { request, resolved } = await opts.agent.askClarification({
        sessionKey: ctx.sessionKey,
        turnId: ctx.turnId,
        question,
        choices: clarificationMeta.choices,
        allowFreeText:
          clarificationMeta.allowFreeText ||
          clarificationMeta.choices.length === 0,
        reason: clarificationMeta.reason,
        riskIfAssumed: clarificationMeta.riskIfAssumed,
        signal: ctx.abortSignal,
        onRequest: (created) => {
          ctx.emit({
            type: "control_event",
            seq: 0,
            event: {
              type: "control_request_created",
              request: created,
            },
          } as Parameters<HookContext["emit"]>[0]);
        },
      });

      if (resolved.state !== "answered") {
        return {
          action: "block",
          reason: `[CLARIFICATION:${resolved.state.toUpperCase()}] ${question}`,
        };
      }

      const clarification = renderClarificationResponse(request, resolved);
      if (!clarification) {
        return {
          action: "block",
          reason: `[CLARIFICATION:EMPTY_ANSWER] ${question}`,
        };
      }

      return {
        action: "replace",
        value: {
          ...args,
          messages: appendClarificationMessage(args.messages, clarification),
        },
      };
    },
  };
}
