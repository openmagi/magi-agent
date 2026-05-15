export type AgentRulesPreviewKind = "harness" | "policy";

export interface AgentRulesPreviewControl {
  id: string;
  kind: AgentRulesPreviewKind;
  title: string;
  summary: string;
  trigger: string;
  action: string;
  enforcement: string;
  sourceText: string;
}

export interface AgentRulesPreview {
  controls: AgentRulesPreviewControl[];
  advisoryRules: string[];
  warnings: string[];
}

const DIRECTIVE_LINE_PREFIX_RE = /^\s*(?:[-*+]\s+|\d+[.)]\s+)?/;
const MARKDOWN_HEADING_RE = /^\s*#+\s+/;

const LANGUAGE_LABELS: Record<string, string> = {
  ko: "Korean",
  en: "English",
  ja: "Japanese",
  zh: "Chinese",
  es: "Spanish",
};

function cleanRuleLine(line: string): string {
  return line
    .replace(MARKDOWN_HEADING_RE, "")
    .replace(DIRECTIVE_LINE_PREFIX_RE, "")
    .trim();
}

function normalizeUserRules(raw: string | null | undefined): string[] {
  if (!raw) return [];
  return raw
    .split("\n")
    .map((line) => cleanRuleLine(line))
    .filter((line) => line.length > 0 && line !== "[truncated]");
}

function isLanguageDirective(text: string): string | null {
  if (
    /(?:always|reply|answer).*(?:korean)|(?:항상|한국어).*(?:답|응답)|한국어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "ko";
  }
  if (
    /(?:always|reply|answer).*(?:english)|(?:항상|영어).*(?:답|응답)|영어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "en";
  }
  if (
    /(?:always|reply|answer).*(?:japanese)|(?:항상|일본어).*(?:답|응답)|일본어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "ja";
  }
  if (
    /(?:always|reply|answer).*(?:chinese)|(?:항상|중국어).*(?:답|응답)|중국어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "zh";
  }
  if (
    /(?:always|reply|answer).*(?:spanish)|(?:항상|스페인어).*(?:답|응답)|스페인어로\s*(?:답변|응답)/i.test(
      text,
    )
  ) {
    return "es";
  }
  return null;
}

function upsertControl(
  controls: AgentRulesPreviewControl[],
  next: AgentRulesPreviewControl,
): void {
  const index = controls.findIndex((item) => item.id === next.id);
  if (index >= 0) {
    controls[index] = next;
    return;
  }
  controls.push(next);
}

function addUniqueControl(
  controls: AgentRulesPreviewControl[],
  next: AgentRulesPreviewControl,
): void {
  if (controls.some((item) => item.id === next.id)) return;
  controls.push(next);
}

function harnessControl(line: string): AgentRulesPreviewControl | null {
  if (
    /(?=.*(?:파일|문서|리포트|보고서|artifact|file|document|report))(?=.*(?:만들|생성|작성|create|generate|write))(?=.*(?:첨부|채팅|전달|attach|attachment|deliver|send))/i.test(
      line,
    )
  ) {
    return {
      id: "user-harness:file-delivery-after-create",
      kind: "harness",
      title: "File delivery required",
      summary: "When a file or artifact is created, the runtime requires FileDeliver before completion.",
      trigger: "beforeCommit",
      action: "Require FileDeliver",
      enforcement: "block_on_fail",
      sourceText: line,
    };
  }

  if (
    /(?:최종\s*답변|답변\s*전|final\s+answer|before\s+(?:the\s+)?answer|before\s+final).*(?:검사|확인|검증|verify|check|double.?check)|(?:한\s*번\s*더|다시).*(?:검사|확인|검증|verify|check)/i.test(
      line,
    )
  ) {
    return {
      id: "user-harness:final-answer-verifier",
      kind: "harness",
      title: "Final answer verifier",
      summary: "Before completion, a verifier checks whether the answer satisfies the user request.",
      trigger: "beforeCommit",
      action: "Run LLM verifier",
      enforcement: "block_on_fail",
      sourceText: line,
    };
  }

  if (
    /(?:출처|근거|citation|citations|source|sources).*(?:확인|검사|검증|명시|포함|check|verify|include|cite)|(?:확인|검사|검증|check|verify).*(?:출처|근거|citation|source)/i.test(
      line,
    )
  ) {
    return {
      id: "user-harness:source-grounding-verifier",
      kind: "harness",
      title: "Source grounding verifier",
      summary: "Before completion, a verifier checks whether factual claims are grounded in named sources.",
      trigger: "beforeCommit",
      action: "Run LLM verifier",
      enforcement: "block_on_fail",
      sourceText: line,
    };
  }

  if (
    /(?=.*(?:before|prior|먼저|전에|전에는))(?=.*(?:email|upload|payment|paying|posting|publicly|external|이메일|업로드|결제|공개|게시|외부))(?=.*(?:confirm|confirmation|ask|approve|확인|허락|승인|물어))/i.test(
      line,
    )
  ) {
    return {
      id: "user-harness:external-action-confirmation",
      kind: "harness",
      title: "External action confirmation",
      summary: "Before email, external upload, payment, or public posting, the runtime asks for user confirmation.",
      trigger: "beforeExternalAction",
      action: "Ask confirmation",
      enforcement: "block_until_confirmed",
      sourceText: line,
    };
  }

  return null;
}

export function compileAgentRulesPreview(
  rawRules: string | null | undefined,
): AgentRulesPreview {
  const controls: AgentRulesPreviewControl[] = [];
  const advisoryRules: string[] = [];
  const warnings: string[] = [];
  const lines = normalizeUserRules(rawRules);

  for (const line of lines) {
    const harness = harnessControl(line);
    if (harness) {
      addUniqueControl(controls, harness);
      continue;
    }

    const language = isLanguageDirective(line);
    if (language) {
      const existing = controls.find((item) => item.id === "policy:response-language");
      const languageLabel = LANGUAGE_LABELS[language] ?? language;
      if (existing && existing.summary !== `Respond in ${languageLabel}.`) {
        warnings.push(
          `Conflicting response language directives detected; keeping ${languageLabel}.`,
        );
      }
      upsertControl(controls, {
        id: "policy:response-language",
        kind: "policy",
        title: "Response language",
        summary: `Respond in ${languageLabel}.`,
        trigger: "everyTurn",
        action: "Set response language",
        enforcement: "runtime_policy",
        sourceText: line,
      });
      continue;
    }

    if (
      /(?:page\s*number|페이지\s*번호).*(?:cit|인용|출처)|(?:cit|인용|출처).*(?:page\s*number|페이지\s*번호)/i.test(
        line,
      )
    ) {
      upsertControl(controls, {
        id: "policy:citations-page-numbers",
        kind: "policy",
        title: "Citations with page numbers",
        summary: "Require cited sources and page numbers where citations are used.",
        trigger: "everyTurn",
        action: "Require source citations",
        enforcement: "runtime_policy",
        sourceText: line,
      });
      continue;
    }

    if (
      /(?:long[-\s]*running|long\s+work|progress\s+updates?|go\s+silent|오래\s*걸|긴\s*작업|진행\s*상황|중간\s*(?:진행|보고)|조용히\s*멈)/i.test(
        line,
      )
    ) {
      addUniqueControl(controls, {
        id: "policy:progress-updates",
        kind: "policy",
        title: "Progress updates",
        summary: "Provide brief progress updates during long-running work instead of going silent.",
        trigger: "duringLongTask",
        action: "Send progress updates",
        enforcement: "runtime_policy",
        sourceText: line,
      });
      continue;
    }

    if (/(?:cit|source|출처|인용)/i.test(line)) {
      addUniqueControl(controls, {
        id: "policy:citations-sources",
        kind: "policy",
        title: "Source citations",
        summary: "Require cited or named sources where claims need support.",
        trigger: "everyTurn",
        action: "Require source citations",
        enforcement: "runtime_policy",
        sourceText: line,
      });
      continue;
    }

    if (/(?:no\s*profanity|don't\s*swear|do not swear|비속어\s*금지|욕설\s*금지)/i.test(line)) {
      addUniqueControl(controls, {
        id: "policy:no-profanity",
        kind: "policy",
        title: "No profanity",
        summary: "Block profanity in the response mode policy.",
        trigger: "everyTurn",
        action: "Set response policy",
        enforcement: "runtime_policy",
        sourceText: line,
      });
      continue;
    }

    if (/(?:be\s*concise|brief|concise|간결|짧게)/i.test(line)) {
      addUniqueControl(controls, {
        id: "policy:concise",
        kind: "policy",
        title: "Concise responses",
        summary: "Prefer concise answers in the response mode policy.",
        trigger: "everyTurn",
        action: "Set response policy",
        enforcement: "runtime_policy",
        sourceText: line,
      });
      continue;
    }

    advisoryRules.push(line);
  }

  return { controls, advisoryRules, warnings };
}
