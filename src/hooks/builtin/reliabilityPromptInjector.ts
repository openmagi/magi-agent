/**
 * Reliability prompt injector.
 *
 * Lightweight native trigger layer for reliability skills. It does not
 * paste full skill bodies; it injects compact, high-priority reminders
 * when the current user message calls for debugging discipline,
 * evidence routing, self-model correction, task contracts, or async
 * delivery care.
 */

import type { HookContext, RegisteredHook } from "../types.js";
import type { LLMMessage } from "../../transport/LLMClient.js";
import { latestUserText } from "./classifyTurnMode.js";

const DEBUG_RE = /(?:bug|error|exception|failed?|failing|breaks?|regression|not working|안\s*됨|안돼|오류|에러|실패|깨졌|버그|고장|빌드|테스트)/i;
const EVIDENCE_RE = /(?:latest|current|today|recent|source|citation|verify|look\s*up|search|find|web|url|pdf|document|file|upload|kb|knowledge|최신|현재|오늘|검색|찾아|출처|인용|검증|확인|문서|파일|업로드|자료|근거)/i;
const SELF_MODEL_RE = /(?:what can you do|capabilit|permission|environment|workspace|pricing|price|plan|policy|너.*(?:할 수|가능)|기능|권한|환경|워크스페이스|가격|요금|정책|행동\s*방식|프롬프트|스킬)/i;
const FRUSTRATION_RE = /(?:again|still|wrong|you said|why didn't|frustrat|아직도|또|틀렸|왜\s*안|말했잖|답답|제대로)/i;
const CONTRACT_RE = /<task_contract\b|verification_mode|acceptance_criteria|검증\s*모드|수락\s*기준/i;
const ASYNC_RE = /(?:later|notify|when done|background|cron|schedule|remind|나중|완료되면|알려줘|백그라운드|크론|예약|리마인드)/i;

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_RELIABILITY_PROMPT;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export function buildReliabilityPolicyBlock(userText: string): string {
  const lines: string[] = [];
  const text = userText.trim();
  if (!text) return "";

  if (DEBUG_RE.test(text)) {
    lines.push(
      "- Use systematic-debugging: reproduce or inspect first, identify evidence, then fix the smallest proven cause.",
      "- Use verification-before-completion before claiming a fix; run or report the relevant check.",
    );
  }
  if (EVIDENCE_RE.test(text)) {
    lines.push(
      "- Use evidence-router: choose current sources, KB/search, file reads, or document extraction before factual claims.",
      "- Cite or name the evidence source when the user needs accuracy, freshness, legal, financial, or operational facts.",
    );
  }
  if (SELF_MODEL_RE.test(text)) {
    lines.push(
      "- Use meta-cognition: verify your own runtime, permissions, tools, prices, and platform behavior from available sources before asserting them.",
    );
  }
  if (FRUSTRATION_RE.test(text)) {
    lines.push(
      "- Use frustration-resolution: acknowledge the specific miss, re-check the evidence, and avoid repeating the failed approach.",
    );
  }
  if (CONTRACT_RE.test(text)) {
    lines.push(
      "- Use task-contract-orchestration: preserve acceptance criteria and verification_mode exactly; full means exhaustive, not sampled.",
    );
  }
  if (ASYNC_RE.test(text)) {
    lines.push(
      "- Use async-work-monitoring: do not promise future notification unless a real scheduled/background mechanism is created and verified.",
    );
  }

  if (lines.length === 0) return "";
  return `<reliability-policy>\n${lines.join("\n")}\n</reliability-policy>`;
}

export function makeReliabilityPromptInjectorHook(): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:reliability-prompt-injector",
    point: "beforeLLMCall",
    priority: 6,
    blocking: true,
    timeoutMs: 200,
    handler: async (args, _ctx: HookContext) => {
      if (!isEnabled()) return { action: "continue" };
      if (args.iteration > 0) return { action: "continue" };

      const userText = latestUserText(args.messages as readonly LLMMessage[]);
      if (!userText) return { action: "continue" };

      const block = buildReliabilityPolicyBlock(userText);
      if (!block) return { action: "continue" };

      return {
        action: "replace",
        value: {
          ...args,
          system: `${args.system}\n\n${block}`,
        },
      };
    },
  };
}

export const reliabilityPromptInjectorHook = makeReliabilityPromptInjectorHook();
