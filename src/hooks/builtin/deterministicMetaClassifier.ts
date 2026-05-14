/**
 * P3: Deterministic meta-classifier — keyword/regex/state-based replacement
 * for the Haiku-powered classifyRequestMeta(). Same output interface.
 *
 * Env gate: MAGI_DETERMINISTIC_META=1
 */

import type { RequestMetaClassificationResult } from "../../execution/ExecutionContract.js";
import { defaultRequestMeta } from "./turnMetaClassifier.js";

const CODING_KEYWORDS = /\b(?:code|fix|bug|test|debug|refactor|deploy|commit|PR|branch|lint|build|compile|implement|function|class|module|import|error|exception|stack\s*trace|type(?:script|error)|eslint)\b/i;
const CODING_KEYWORDS_KO = /(?:코드|버그|테스트|디버그|리팩토|배포|커밋|브랜치|빌드|구현|함수|클래스|에러|오류)/;

const EXPLORATORY_KEYWORDS = /\b(?:experiment|try|prototype|explore|research|investigate|look\s+into|brainstorm|design|approach|strategy|plan|architecture)\b/i;
const EXPLORATORY_KEYWORDS_KO = /(?:실험|시도|프로토타입|탐색|조사|연구|설계|접근|전략|계획|아키텍처)/;

const SKIP_TDD_KEYWORDS = /\b(?:skip\s+(?:tdd|test)|no\s+test|without\s+test|don'?t\s+(?:write\s+)?test)/i;

const DETERMINISTIC_KEYWORDS = /\b(?:exact|precisely|specific\s+number|how\s+many|count|total|calculate|compute|sum|average)\b/i;
const DETERMINISTIC_KEYWORDS_KO = /(?:정확|몇\s*개|몇\s*명|총|합계|평균|계산)/;

const FILE_DELIVERY_KEYWORDS = /\b(?:send|deliver|download|export|save\s+as|generate\s+(?:pdf|docx|hwpx|csv|xlsx))\b/i;
const FILE_DELIVERY_KEYWORDS_KO = /(?:보내|전달|다운로드|내보내|저장|파일|PDF|DOCX|HWPX|CSV|XLSX)/;
const FILE_DELIVERY_PATH_RE = /\b[\w/.-]+\.(?:pdf|docx|hwpx|csv|xlsx|md|txt|json|html)\b/i;

const PLANNING_KEYWORDS = /\b(?:plan|step[\s-]by[\s-]step|roadmap|design|architect|strategy|phased|milestone)\b/i;
const PLANNING_KEYWORDS_KO = /(?:계획|단계별|로드맵|설계|전략|마일스톤)/;

const MEMORY_REDACT_KEYWORDS = /\b(?:forget|delete\s+memory|remove\s+from\s+memory|erase)\b/i;
const MEMORY_REDACT_KEYWORDS_KO = /(?:잊어|기억\s*삭제|메모리\s*삭제|기억\s*제거)/;

const DOCUMENT_KEYWORDS = /\b(?:document|report|memo|letter|essay|draft|write|compose|create\s+(?:a\s+)?(?:pdf|docx|hwpx|document|report))\b/i;
const DOCUMENT_KEYWORDS_KO = /(?:문서|보고서|메모|편지|에세이|초안|작성|생성)/;

export function classifyRequestMetaDeterministic(
  userMessage: string,
): RequestMetaClassificationResult {
  const result = defaultRequestMeta("deterministic_classifier");
  const text = userMessage;

  // turnMode
  const isCoding = CODING_KEYWORDS.test(text) || CODING_KEYWORDS_KO.test(text);
  const isExploratory = EXPLORATORY_KEYWORDS.test(text) || EXPLORATORY_KEYWORDS_KO.test(text);
  if (isCoding) {
    result.turnMode = { label: "coding", confidence: 0.8 };
  } else if (isExploratory) {
    result.turnMode = { label: "exploratory", confidence: 0.7 };
  }

  // skipTdd
  result.skipTdd = SKIP_TDD_KEYWORDS.test(text);

  // implementationIntent
  result.implementationIntent = isCoding && /\b(?:implement|build|create|add|make)\b/i.test(text);

  // documentOrFileOperation
  result.documentOrFileOperation = DOCUMENT_KEYWORDS.test(text) || DOCUMENT_KEYWORDS_KO.test(text);

  // deterministic
  if (DETERMINISTIC_KEYWORDS.test(text) || DETERMINISTIC_KEYWORDS_KO.test(text)) {
    result.deterministic = {
      requiresDeterministic: true,
      kinds: [],
      reason: "deterministic keywords detected",
      suggestedTools: ["Bash"],
      acceptanceCriteria: [],
    };
  }

  // fileDelivery
  if (FILE_DELIVERY_KEYWORDS.test(text) || FILE_DELIVERY_KEYWORDS_KO.test(text)) {
    const pathMatch = FILE_DELIVERY_PATH_RE.exec(text);
    result.fileDelivery = {
      intent: "deliver_existing",
      path: pathMatch?.[0] ?? null,
      wantsChatDelivery: true,
      wantsKbDelivery: false,
      wantsFileOutput: true,
    };
  }

  // planning
  const isLong = text.split(/\s+/).length > 50;
  if (PLANNING_KEYWORDS.test(text) || PLANNING_KEYWORDS_KO.test(text) || isLong) {
    result.planning = {
      need: isLong ? "task_board" : "inline",
      reason: isLong ? "complex request detected" : "planning keywords detected",
      suggestedStrategy: "Break into steps.",
    };
  }

  // memoryMutation
  if (MEMORY_REDACT_KEYWORDS.test(text) || MEMORY_REDACT_KEYWORDS_KO.test(text)) {
    result.memoryMutation = {
      intent: "redact",
      target: null,
      rawFileRedactionRequested: false,
      reason: "memory redaction keywords detected",
    };
  }

  return result;
}
