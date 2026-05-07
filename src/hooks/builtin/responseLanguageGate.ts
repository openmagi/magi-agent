/**
 * Response language gate.
 *
 * Bot language selection is a runtime policy, not only prompt text.
 * This beforeCommit gate asks a small LLM judge whether the drafted
 * answer follows that policy while allowing legitimate cross-language
 * content such as source titles, quotes, code, names, URLs, or explicit
 * language-learning / translation requests.
 */

import type { PolicyKernel } from "../../policy/PolicyKernel.js";
import type {
  ResponseLanguagePolicy,
  SupportedLanguage,
} from "../../policy/policyTypes.js";
import type { HookContext, RegisteredHook } from "../types.js";

const MAX_RETRIES = 1;
const DEFAULT_TIMEOUT_MS = 8_000;

export interface ResponseLanguageGateOptions {
  policy: Pick<PolicyKernel, "current">;
}

export interface LanguageVerdict {
  pass: boolean;
  detail: string;
}

export function parseLanguageVerdict(raw: string): LanguageVerdict {
  const trimmed = raw.trim();
  const upper = trimmed.toUpperCase();
  if (upper.startsWith("FAIL")) {
    return { pass: false, detail: trimmed || "FAIL" };
  }
  if (upper.startsWith("PASS")) {
    return { pass: true, detail: trimmed || "PASS" };
  }
  return { pass: true, detail: trimmed || "unparseable verifier output; fail-open" };
}

type LanguageStats = {
  hangul: number;
  kana: number;
  cjk: number;
  latin: number;
  spanishSignal: number;
};

interface ResolvedLanguagePolicy {
  original: ResponseLanguagePolicy;
  target: ResponseLanguagePolicy;
  reason: string;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_RESPONSE_LANGUAGE_GATE;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function languageName(language: ResponseLanguagePolicy): string {
  switch (language) {
    case "auto":
      return "Auto";
    case "ko":
      return "Korean";
    case "en":
      return "English";
    case "ja":
      return "Japanese";
    case "zh":
      return "Chinese";
    case "es":
      return "Spanish";
  }
}

function languageDescription(language: ResponseLanguagePolicy): string {
  switch (language) {
    case "auto":
      return "auto - the assistant should use the user's message language as the primary answer language unless the user explicitly asks for another output language.";
    case "ko":
      return "ko - Korean should be the primary explanation language.";
    case "en":
      return "en - English should be the primary explanation language.";
    case "ja":
      return "ja - Japanese should be the primary explanation language.";
    case "zh":
      return "zh - Chinese should be the primary explanation language.";
    case "es":
      return "es - Spanish should be the primary explanation language.";
  }
}

function stripNonProse(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`[^`]*`/g, " ")
    .replace(/\[[^\]\n]{0,80}\]\([^)]*\)/g, " ")
    .replace(/https?:\/\/\S+/gi, " ")
    .replace(/\[META:[^\]\n]*\]/gi, " ")
    .replace(/[\w.-]+\/[\w./-]+/g, " ");
}

function languageStats(text: string): LanguageStats {
  const stats: LanguageStats = {
    hangul: 0,
    kana: 0,
    cjk: 0,
    latin: 0,
    spanishSignal: 0,
  };
  for (const char of stripNonProse(text)) {
    if (/\p{Script=Hangul}/u.test(char)) {
      stats.hangul += 1;
    } else if (/\p{Script=Hiragana}|\p{Script=Katakana}/u.test(char)) {
      stats.kana += 1;
    } else if (/\p{Script=Han}/u.test(char)) {
      stats.cjk += 1;
    } else if (/[A-Za-zÀ-ÖØ-öø-ÿ]/u.test(char)) {
      stats.latin += 1;
      if (/[áéíóúüñ¿¡]/iu.test(char)) stats.spanishSignal += 1;
    }
  }
  const spanishWords = ` ${stripNonProse(text).toLowerCase()} `.match(
    /\b(?:el|la|los|las|un|una|que|para|por|con|como|gracias|hola|usted|ustedes|español)\b/g,
  );
  stats.spanishSignal += spanishWords?.length ?? 0;
  return stats;
}

function detectPrimaryLanguage(text: string): SupportedLanguage | null {
  const stats = languageStats(text);
  if (stats.kana >= 2) return "ja";
  if (stats.hangul >= 2 && stats.hangul >= stats.latin * 0.35) return "ko";
  if (stats.cjk >= 2 && stats.cjk >= stats.latin * 0.5) return "zh";
  if (stats.latin >= 8) {
    return stats.spanishSignal >= 2 ? "es" : "en";
  }
  if (
    stats.latin >= 3 &&
    stats.hangul === 0 &&
    stats.kana === 0 &&
    stats.cjk === 0
  ) {
    return "en";
  }
  return null;
}

function detectRequestedOutputLanguage(userMessage: string): SupportedLanguage | null {
  const text = userMessage.toLowerCase();
  if (
    /(?:in|into)\s+(?:korean|한국어)|(?:korean|한국어)\s+(?:answer|reply|response|version|translation|draft)|한국어로/u.test(
      text,
    )
  ) {
    return "ko";
  }
  if (
    /(?:in|into)\s+english|english\s+(?:answer|reply|response|version|translation|draft)|영어로|영문(?:으로)?|영어\s*(?:이메일|메일|초안|답변|응답|버전|번역|작성)/u.test(
      text,
    )
  ) {
    return "en";
  }
  if (
    /(?:in|into)\s+japanese|japanese\s+(?:answer|reply|response|version|translation|draft)|일본어로/u.test(
      text,
    )
  ) {
    return "ja";
  }
  if (
    /(?:in|into)\s+chinese|chinese\s+(?:answer|reply|response|version|translation|draft)|중국어로/u.test(
      text,
    )
  ) {
    return "zh";
  }
  if (
    /(?:in|into)\s+spanish|spanish\s+(?:answer|reply|response|version|translation|draft)|스페인어로/u.test(
      text,
    )
  ) {
    return "es";
  }
  return null;
}

function resolveLanguagePolicy(
  language: ResponseLanguagePolicy,
  userMessage: string,
): ResolvedLanguagePolicy {
  if (language !== "auto") {
    return {
      original: language,
      target: language,
      reason: `fixed policy is ${languageName(language)}`,
    };
  }

  const requested = detectRequestedOutputLanguage(userMessage);
  if (requested) {
    return {
      original: language,
      target: requested,
      reason: `explicit output-language request is ${languageName(requested)}`,
    };
  }

  const detected = detectPrimaryLanguage(userMessage);
  if (detected) {
    return {
      original: language,
      target: detected,
      reason: `latest user message is ${languageName(detected)}`,
    };
  }

  return {
    original: language,
    target: "auto",
    reason: "latest user message language was ambiguous",
  };
}

function detectAssistantLanguageMismatch(
  resolved: ResolvedLanguagePolicy,
  assistantText: string,
): string | null {
  if (resolved.target === "auto") return null;

  const detected = detectPrimaryLanguage(assistantText);
  if (!detected || detected === resolved.target) return null;

  const stats = languageStats(assistantText);
  const hasEnoughEvidence =
    (detected === "ko" && stats.hangul >= 8) ||
    (detected === "en" && stats.latin >= 20) ||
    (detected === "ja" && stats.kana >= 4) ||
    (detected === "zh" && stats.cjk >= 4) ||
    (detected === "es" && stats.latin >= 20);
  if (!hasEnoughEvidence) return null;

  return `${resolved.reason}; assistant main prose appears to be ${languageName(detected)}`;
}

export async function judgeResponseLanguage(
  ctx: HookContext,
  input: {
    language: ResponseLanguagePolicy;
    userMessage: string;
    assistantText: string;
    timeoutMs?: number;
  },
): Promise<LanguageVerdict> {
  const resolved = resolveLanguagePolicy(input.language, input.userMessage);
  const prompt = [
    "You are a runtime verifier for response language policy.",
    "Use semantic judgment across languages. Do not rely on keyword matching.",
    "Reply with exactly `PASS` or `FAIL: <short reason>`.",
    "",
    `configured language policy: ${input.language}`,
    `target language policy: ${resolved.target}`,
    `resolution: ${resolved.reason}`,
    languageDescription(resolved.target),
    "",
    "Pass-through exceptions that should usually PASS:",
    "- quoted source titles, article names, citations, references, URLs, filenames, code, API names, product names, or proper nouns in their original language",
    "- translation, language learning, proofreading, email drafting, or other tasks where the user explicitly asks for output in another language",
    "- tables or source lists where individual source metadata is naturally in another language",
    "- a short foreign-language phrase embedded in an answer whose main explanatory prose follows the policy",
    "",
    "Fail when the main user-facing explanation ignores the policy, such as mostly English prose under Korean policy, or an auto policy answer not matching the user's request language.",
    "",
    `USER MESSAGE:\n${input.userMessage.slice(0, 3000)}`,
    "",
    `ASSISTANT DRAFT:\n${input.assistantText.slice(0, 5000)}`,
  ].join("\n");

  let output = "";
  try {
    const stream = ctx.llm.stream({
      model: ctx.agentModel,
      system:
        "Runtime language-policy verifier. Return only PASS or FAIL with a brief reason. No tools.",
      messages: [{ role: "user", content: prompt }],
      max_tokens: 80,
      temperature: 0,
      signal: ctx.abortSignal,
    });
    const deadline = Date.now() + Math.max(500, input.timeoutMs ?? DEFAULT_TIMEOUT_MS);
    for await (const event of stream) {
      if (Date.now() > deadline) break;
      if (event.kind === "text_delta") output += event.delta;
      if (event.kind === "message_end" || event.kind === "error") break;
    }
  } catch {
    return { pass: true, detail: "verifier failed; fail-open" };
  }
  return parseLanguageVerdict(output);
}

async function loadLanguagePolicy(
  opts: ResponseLanguageGateOptions,
  ctx: HookContext,
): Promise<ResponseLanguagePolicy | null> {
  try {
    return (await opts.policy.current()).policy.responseMode.language ?? null;
  } catch (err) {
    ctx.log("warn", "[response-language-gate] policy load failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return null;
  }
}

export function makeResponseLanguageGateHook(
  opts: ResponseLanguageGateOptions,
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:response-language-gate",
    point: "beforeCommit",
    priority: 85,
    blocking: true,
    failOpen: true,
    timeoutMs: DEFAULT_TIMEOUT_MS + 1_000,
    handler: async ({ userMessage, assistantText, retryCount }, ctx) => {
      if (!isEnabled()) return { action: "continue" };
      if (!assistantText.trim() || !userMessage.trim()) return { action: "continue" };

      const language = await loadLanguagePolicy(opts, ctx);
      if (!language) return { action: "continue" };

      const resolved = resolveLanguagePolicy(language, userMessage);
      const deterministicMismatch = detectAssistantLanguageMismatch(
        resolved,
        assistantText,
      );
      const verdict = deterministicMismatch
        ? { pass: false, detail: deterministicMismatch }
        : await judgeResponseLanguage(ctx, {
            language,
            userMessage,
            assistantText,
          });

      ctx.emit({
        type: "rule_check",
        ruleId: "response-language-gate",
        verdict: verdict.pass ? "ok" : "violation",
        detail:
          `language=${resolved.target} configured=${resolved.original}` +
          ` retryCount=${retryCount} detail=${verdict.detail}`,
      });

      if (verdict.pass) return { action: "continue" };
      if (retryCount >= MAX_RETRIES) {
        ctx.log("warn", "[response-language-gate] retry exhausted; failing open", {
          language,
          detail: verdict.detail,
          retryCount,
        });
        return { action: "continue" };
      }

      return {
        action: "block",
        reason: [
          `[RETRY:RESPONSE_LANGUAGE:${resolved.target}] The draft appears to violate the bot response language policy.`,
          `Verifier: ${verdict.detail}`,
          "Rewrite the main user-facing explanation in the required language. Preserve legitimate original-language content only for quotes, titles, references, URLs, filenames, code, proper nouns, or explicit translation/language-learning output.",
        ].join("\n"),
      };
    },
  };
}
