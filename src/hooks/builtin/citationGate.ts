/**
 * Built-in citation-gate hook (port of
 * infra/docker/api-proxy/citation-gate.js).
 *
 * Detects legal / academic citations in the user message and injects a
 * MANDATORY verification protocol into the system prompt — the model
 * must fetch the primary source via tools before quoting.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { LLMMessage } from "../../transport/LLMClient.js";

const KR_CASE_NUMBER = /\b(?:19|20)\d{2}[가-힣]\d{2,7}(?:\(\s*[가-힣]+\s*\))?/;
const KR_LEGAL_CUE = /(?:대법원|대판|헌재|대법|전원합의체|전합|선고|판결|결정|판례|법제처|law\.go\.kr|casenote)/i;
const STATUTE_ARTICLE = /(?:제\s*\d+\s*조(?:의\s*\d+)?|Article\s+\d+(?:\(\d+\))?)/i;
const DOI = /\b10\.\d{4,9}\/[^\s"'<>]+/i;
const ARXIV = /\barXiv:\d{4}\.\d{4,5}/i;

interface CitationDetection {
  signals: string[];
  kind: "legal" | "academic" | "mixed";
}

function detectCitations(text: string): CitationDetection | null {
  if (!text || text.length < 5 || text.length > 20_000) return null;
  const signals: string[] = [];
  if (KR_CASE_NUMBER.test(text)) signals.push("kr_case_number");
  if (KR_LEGAL_CUE.test(text)) signals.push("kr_legal_cue");
  if (STATUTE_ARTICLE.test(text)) signals.push("statute_article");
  if (DOI.test(text)) signals.push("doi");
  if (ARXIV.test(text)) signals.push("arxiv");
  if (signals.length === 0) return null;

  const isLegal = signals.some((s) =>
    ["kr_case_number", "kr_legal_cue", "statute_article"].includes(s),
  );
  const isAcademic = signals.some((s) => ["doi", "arxiv"].includes(s));
  const kind: CitationDetection["kind"] =
    isLegal && isAcademic ? "mixed" : isLegal ? "legal" : "academic";
  return { signals, kind };
}

function buildCitationGateHint(detection: CitationDetection): string {
  const sources: string[] = [];
  if (detection.kind !== "academic") {
    sources.push("- Korean case law: Supreme Court search (https://glaw.scourt.go.kr) or CaseNote API");
    sources.push("- Korean statutes: https://www.law.go.kr — quote original text verbatim");
  }
  if (detection.kind !== "legal") {
    sources.push("- Papers: DOI resolver (https://doi.org/<id>) or arXiv (https://arxiv.org/abs/<id>)");
  }

  return `<aef_citation_gate priority="critical">
This turn references a CITATION (${detection.signals.join(", ")}).

**MANDATORY PROTOCOL — violate and your answer will be wrong:**

1. Do NOT quote, summarize, or characterize the cited source from memory.
   Training data frequently contains fabricated / paraphrased versions
   of case law / statutes / papers. Quoting from memory WILL mislead the user.

2. For every citation, call a tool FIRST to fetch the primary source:
${sources.join("\n")}

3. If the lookup fails, do NOT substitute memory. Say:
   "[Citation] Original text could not be verified — manual verification required."

4. Only quote content you have verified against the fetched text this turn.

5. Include the source URL + retrieval time in the final answer.
</aef_citation_gate>`;
}

function extractLastUserText(messages: LLMMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m || m.role !== "user") continue;
    if (typeof m.content === "string") return m.content;
    if (Array.isArray(m.content)) {
      const parts: string[] = [];
      for (const b of m.content) {
        if (b && typeof b === "object" && "type" in b && b.type === "text" && "text" in b) {
          parts.push((b as { text: string }).text);
        }
      }
      return parts.join("\n");
    }
  }
  return "";
}

export const citationGateHook: RegisteredHook<"beforeLLMCall"> = {
  name: "builtin:citation-gate",
  point: "beforeLLMCall",
  priority: 20,
  blocking: true,
  timeoutMs: 200,
  handler: async ({ messages, tools, system, iteration }, ctx: HookContext) => {
    // Only inject on the first iteration — subsequent iterations
    // already carry the mandate in `system`.
    if (iteration > 0) return { action: "continue" };
    const userText = extractLastUserText(messages);
    const detection = detectCitations(userText);
    if (!detection) return { action: "continue" };

    ctx.emit({
      type: "rule_check",
      ruleId: "citation-gate",
      verdict: "pending",
      detail: detection.signals.join(","),
    });

    const hint = buildCitationGateHint(detection);
    const nextSystem = system ? `${hint}\n\n${system}` : hint;
    return {
      action: "replace",
      value: { messages, tools, system: nextSystem, iteration },
    };
  },
};
