/**
 * Turn-mode classifier for the Coding Discipline subsystem.
 *
 * LLM-based (Haiku) — replaces the previous regex heuristic for
 * accurate mixed-language (Korean + English) classification.
 *
 * Classifies a user message into:
 *   - `"coding"`:      TDD + git hygiene active
 *   - `"exploratory"`: git hygiene only, no TDD (prototype/throwaway)
 *   - `"other"`:       no discipline (docs, analysis, plain chat)
 */

import type { LLMClient, LLMEvent } from "../transport/LLMClient.js";

export type ModeLabel = "coding" | "exploratory" | "other";

export interface ModeClassification {
  label: ModeLabel;
  confidence: number;
}

const CLASSIFIER_PROMPT = `Classify this user message into exactly one category. Reply with ONLY the label, nothing else.

coding — The user is asking to write, fix, debug, refactor, or test code. Includes: implementing features, fixing bugs, writing tests, PR review, git operations, modifying source files.
Examples: "이 버그 고쳐줘", "함수 작성해", "unit test 추가", "implement the API endpoint", "refactor this class"

exploratory — The user is experimenting, prototyping, or trying something throwaway. NOT production code. Includes: quick scripts, proof of concept, sandbox experiments, "just trying".
Examples: "프로토타입 빠르게 만들어보자", "just for fun, try X", "quick script to test", "sandbox this idea"

other — Everything else: analysis, writing, summarizing, chat, planning, file operations, questions, KB search, document work, general tasks.
Examples: "요약해줘", "이 파일 분석해줘", "일정 정리해", "what does this mean?", "search KB for X"

If unsure between coding and other, choose other. If unsure between coding and exploratory, choose coding.`;

const SKIP_TDD_PROMPT = `Does this message explicitly ask to skip tests or disable testing discipline? Reply YES or NO only.

YES examples: "skip tdd", "no tests", "without tests", "테스트 없이", "TDD 건너뛰기"
NO examples: "write a test", "fix the test", "테스트 추가해줘", anything not explicitly opting out`;

/** Returns true when the user's message explicitly opts out of TDD. */
export async function hasSkipTddSignal(text: string, llm?: LLMClient): Promise<boolean> {
  if (!llm) return false;
  // Quick pre-filter: skip LLM call if no test-related words at all
  if (!/test|tdd|테스트|skip|without|없이|건너/i.test(text)) return false;

  try {
    let result = "";
    for await (const event of llm.stream({
      model: "claude-haiku-4-5",
      system: SKIP_TDD_PROMPT,
      messages: [{ role: "user", content: [{ type: "text", text: text.slice(0, 300) }] }],
      max_tokens: 5,
    })) {
      if (event.kind === "text_delta") result += event.delta;
    }
    return result.trim().toUpperCase().startsWith("YES");
  } catch {
    return false; // Fail-open: don't skip TDD on error
  }
}

/**
 * LLM-based turn mode classifier. Uses Haiku for accurate
 * mixed-language intent classification.
 */
export async function classifyTurnMode(
  text: string,
  llm?: LLMClient,
): Promise<ModeClassification> {
  const trimmed = text.trim();
  if (trimmed.length === 0) {
    return { label: "other", confidence: 1 };
  }

  if (!llm) {
    return { label: "other", confidence: 0.5 };
  }

  try {
    let result = "";
    for await (const event of llm.stream({
      model: "claude-haiku-4-5",
      system: CLASSIFIER_PROMPT,
      messages: [{ role: "user", content: [{ type: "text", text: trimmed.slice(0, 500) }] }],
      max_tokens: 10,
    })) {
      if (event.kind === "text_delta") result += event.delta;
    }

    const label = result.trim().toLowerCase();
    if (label.startsWith("coding")) return { label: "coding", confidence: 0.9 };
    if (label.startsWith("exploratory")) return { label: "exploratory", confidence: 0.9 };
    return { label: "other", confidence: 0.9 };
  } catch {
    return { label: "other", confidence: 0.5 }; // Fail-open
  }
}

/**
 * Convenience wrapper applying the confidence floor.
 * Below the floor, the label is demoted to `other`.
 */
export async function classifyTurnModeGated(
  text: string,
  llm?: LLMClient,
  floor = 0.6,
): Promise<ModeClassification> {
  const raw = await classifyTurnMode(text, llm);
  if (raw.label === "other") return raw;
  if (raw.confidence < floor) {
    return { label: "other", confidence: raw.confidence };
  }
  return raw;
}
