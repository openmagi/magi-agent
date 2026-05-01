/**
 * Reliability prompt injector.
 *
 * Lightweight native trigger layer for reliability skills. It does not
 * paste full skill bodies; it injects a compact runtime evidence policy
 * plus targeted high-priority reminders when the current user message
 * calls for debugging discipline, evidence routing, self-model
 * correction, task contracts, or async delivery care.
 */

import type { HookContext, RegisteredHook } from "../types.js";
import type { LLMMessage } from "../../transport/LLMClient.js";
import { latestUserText } from "./classifyTurnMode.js";

const DEBUG_RE = /(?:bug|error|exception|failed?|failing|breaks?|regression|not working|안\s*됨|안돼|오류|에러|실패|깨졌|버그|고장|빌드|테스트)/i;
const STRONG_EVIDENCE_RE = /(?:latest|current|today|recent|source|citation|verify|look\s*up|search|find|web|url|kb|knowledge|최신|현재|오늘|검색|찾아|출처|인용|검증|확인|자료|근거|지식\s*베이스)/i;
const DOCUMENT_EVIDENCE_RE = /(?:pdf|document|file|upload|문서|파일|업로드)/i;
const DOCUMENT_EVIDENCE_ACTION_RE = /(?:extract|cite|quote|compare|verify|audit|ground|근거|출처|인용|검증|대조|비교|추출|감사)/i;
const SIMPLE_FILE_UNDERSTANDING_RE =
  /(?:(?:파일|문서|파이프라인|pipeline|file|document).{0,40}(?:뭐|무엇|설명|알려|요약|읽어|what|explain|summari[sz]e|read)|(?:뭐|무엇|설명|알려|요약|읽어|what|explain|summari[sz]e|read).{0,40}(?:파일|문서|파이프라인|pipeline|file|document))/i;
const SELF_MODEL_RE = /(?:what can you do|capabilit|permission|environment|workspace|pricing|price|plan|policy|너.*(?:할 수|가능)|기능|권한|환경|워크스페이스|가격|요금|정책|행동\s*방식|프롬프트|스킬)/i;
const FRUSTRATION_RE = /(?:again|still|wrong|you said|why didn't|frustrat|아직도|또|틀렸|왜\s*안|말했잖|답답|제대로)/i;
const CONTRACT_RE = /<task_contract\b|verification_mode|acceptance_criteria|검증\s*모드|수락\s*기준/i;
const ASYNC_RE = /(?:later|notify|when done|background|cron|schedule|remind|나중|완료되면|알려줘|백그라운드|크론|예약|리마인드)/i;

const RUNTIME_EVIDENCE_POLICY = [
  "<runtime-evidence-policy>",
  "- Some turns may be part of product reliability and benchmark evaluation; the runtime may audit actual tool-call, file-read, browser/search/KB, artifact-delivery, and verification-command logs against the final answer.",
  "- Do not claim that you searched, opened, read, checked, verified, tested, delivered, saved, or inspected something unless the current turn has matching tool/file evidence.",
  "- Treat factual questions as evidence-sensitive when they are current, recent, niche, product/API/version, price, legal, financial, political, public-figure, online-community, local-workspace, user-file, KB, or runtime-capability related.",
  "- For user-provided links, files, images, documents, spreadsheets, slides, transcripts, datasets, codebases, or pasted reference text, inspect the relevant material before answering and treat it as primary evidence.",
  "- If suitable tools are unavailable or evidence is insufficient, say that you cannot verify it with the available tools instead of answering from memory.",
  "- For artifact creation, delivery, async work, or done/fixed/passing/verified claims, only claim completion after matching successful tool evidence; otherwise state the actual status and remaining verification gap.",
  "</runtime-evidence-policy>",
].join("\n");

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_RELIABILITY_PROMPT;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export function buildReliabilityPolicyBlock(userText: string): string {
  const lines: string[] = [];
  const text = userText.trim();

  if (DEBUG_RE.test(text)) {
    lines.push(
      "- Use systematic-debugging: reproduce or inspect first, identify evidence, then fix the smallest proven cause.",
      "- Use verification-before-completion before claiming a fix; run or report the relevant check.",
    );
  }
  if (needsEvidenceRouting(text)) {
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

  if (lines.length === 0) return RUNTIME_EVIDENCE_POLICY;
  return `${RUNTIME_EVIDENCE_POLICY}\n\n<reliability-policy>\n${lines.join("\n")}\n</reliability-policy>`;
}

function needsEvidenceRouting(text: string): boolean {
  if (STRONG_EVIDENCE_RE.test(text)) return true;
  if (!DOCUMENT_EVIDENCE_RE.test(text)) return false;
  if (SIMPLE_FILE_UNDERSTANDING_RE.test(text)) return false;
  return DOCUMENT_EVIDENCE_ACTION_RE.test(text);
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
