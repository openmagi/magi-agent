import type { ChatResponseLanguage } from "./types";

export interface PublicToolPreviewInput {
  label: string;
  inputPreview?: string;
  outputPreview?: string;
  language?: ChatResponseLanguage;
}

export interface PublicToolPreview {
  action: string;
  target?: string;
  snippet?: string;
}

const MAX_SNIPPET_LENGTH = 240;
const MAX_TARGET_LENGTH = 180;
const MAX_URL_DISPLAY_LENGTH = 96;
const MAX_URL_PATH_SEGMENTS = 3;
const URL_RE = /\bhttps?:\/\/[^\s"'<>),\]}]+/i;
const URL_GLOBAL_RE = /\bhttps?:\/\/[^\s"'<>),\]}]+/gi;
const SENSITIVE_URL_PATH_RE =
  /(?:^|\/)(?:auth|callback|callbacks|cookie|oauth|oauth2|sessions?|tokens?)(?:[/?#]|$)/i;
const SECRET_SHAPE_RE =
  /(?:^|[^a-z0-9])(?:sk-[a-z0-9_-]{6,}|sk-proj-[a-z0-9_-]{6,}|[rs]k_(?:live|test)_[a-z0-9_]{8,}|github_pat_[a-z0-9_]{12,}|gh[pousr]_[a-z0-9_]{12,}|xox[abprs]-[a-z0-9-]{12,}|akia[0-9a-z]{16}|eyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})(?:$|[^a-z0-9])/i;
const SECRET_URL_PATH_VALUE_RE =
  /\b(?:api[._-]?key|access[._-]?token|authorization|bearer|client[._-]?secret|connector[._-]?token|id[._-]?token|password|refresh[._-]?token|secret|session[._-]?id)\b/i;
const PRIVATE_PROGRESS_TEXT_RE =
  /\b(?:api[._-]?key|auth(?:orization)?|bearer|cookie|hidden|private|prompt|raw|secret|session|token|tool[._-]?(?:args?|logs?|results?)|transcript)\b/i;
const URL_VALUE_KEYS = [
  "url",
  "uri",
  "source",
  "sourceUrl",
  "source_url",
  "resultUrl",
  "result_url",
  "canonicalUrl",
  "canonical_url",
  "link",
  "href",
];
const URL_TEXT_KEYS = ["target", "detail", "summary", "message"];
const URL_CONTAINER_KEYS = [
  "args",
  "arguments",
  "data",
  "items",
  "meta",
  "output",
  "page",
  "pages",
  "progress",
  "result",
  "results",
  "source",
  "sources",
];

type PreviewObject = Record<string, unknown>;

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function localized(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

const INTERNAL_STRUCTURED_KEYS = new Set([
  "artifactid",
  "attempts",
  "durationms",
  "exitcode",
  "ignoredcount",
  "internalid",
  "isoduration",
  "numericcount",
  "signal",
  "spawndir",
  "stderr",
  "stdout",
  "taskid",
  "timestampms",
  "toolcallcount",
  "truncated",
]);

function redact(value: string): string {
  return value
    .replace(/(Bearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1[redacted]")
    .replace(/\bgh[pousr]_[A-Za-z0-9_]+\b/g, "[redacted]")
    .replace(/\bsk-[A-Za-z0-9_-]+\b/g, "[redacted]")
    .replace(
      /((?:api[_-]?key|token|secret|password)["'\s:=]+)([^"'\s,}]+)/gi,
      "$1[redacted]",
    );
}

function bounded(value: string, maxLength: number): string {
  const clean = redact(value).trim();
  if (clean.length <= maxLength) return clean;
  return `${clean.slice(0, Math.max(0, maxLength - 3)).trimEnd()}...`;
}

function normalizedUrlPath(value: string): string {
  return value
    .replace(/%(?:2f|5c)/gi, "/")
    .replace(/%3f/gi, "?")
    .replace(/%23/gi, "#");
}

function shortUrlPath(pathname: string): string {
  const segments = pathname.split("/").filter(Boolean);
  if (segments.length === 0) return "";
  return `/${segments.slice(0, MAX_URL_PATH_SEGMENTS).join("/")}`;
}

function boundedUrlDisplay(host: string, pathname: string): string {
  const value = `${host}${pathname}`;
  if (value.length <= MAX_URL_DISPLAY_LENGTH) return value;

  const availablePathLength = Math.max(0, MAX_URL_DISPLAY_LENGTH - host.length - 3);
  if (availablePathLength <= 1) {
    return bounded(host, MAX_URL_DISPLAY_LENGTH);
  }
  return `${host}${pathname.slice(0, availablePathLength).replace(/\/?$/, "")}...`;
}

function safePublicUrl(value?: string): string | undefined {
  const trimmed = value?.trim();
  if (!trimmed) return undefined;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return undefined;
    if (parsed.username || parsed.password) return undefined;

    const host = parsed.host;
    if (!host || redact(host) !== host || SECRET_SHAPE_RE.test(host)) return undefined;

    const normalizedPath = normalizedUrlPath(parsed.pathname);
    if (SENSITIVE_URL_PATH_RE.test(normalizedPath)) return host;
    if (
      SECRET_URL_PATH_VALUE_RE.test(normalizedPath) ||
      SECRET_SHAPE_RE.test(normalizedPath)
    ) {
      return undefined;
    }

    const pathname = shortUrlPath(parsed.pathname);
    const publicUrl = boundedUrlDisplay(host, pathname);
    if (redact(publicUrl) !== publicUrl || SECRET_SHAPE_RE.test(publicUrl)) return undefined;
    return publicUrl;
  } catch {
    return undefined;
  }
}

function safePublicUrlFromText(value?: string): string | undefined {
  if (!value) return undefined;
  return safePublicUrl(value.match(URL_RE)?.[0]);
}

function safeProgressDetail(value?: string): string | undefined {
  const normalized = value
    ?.replace(/\s+/g, " ")
    .trim()
    .replace(URL_GLOBAL_RE, (url) => safePublicUrl(url) ?? "[redacted url]");
  if (!normalized) return undefined;
  if (PRIVATE_PROGRESS_TEXT_RE.test(normalized) || redact(normalized) !== normalized) {
    return undefined;
  }
  return bounded(normalized, MAX_SNIPPET_LENGTH);
}

function boundedBrowserUrlDisplay(origin: string, pathname: string): string {
  const value = `${origin}${pathname}`;
  if (value.length <= MAX_URL_DISPLAY_LENGTH) return value;

  const availablePathLength = Math.max(0, MAX_URL_DISPLAY_LENGTH - origin.length - 3);
  if (availablePathLength <= 1) {
    return bounded(origin, MAX_URL_DISPLAY_LENGTH);
  }
  return `${origin}${pathname.slice(0, availablePathLength).replace(/\/?$/, "")}...`;
}

function safeBrowserAbsoluteUrl(value?: string): string | undefined {
  const trimmed = value?.trim();
  if (!trimmed) return undefined;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return undefined;
    if (parsed.username || parsed.password) return undefined;

    const host = parsed.host;
    if (!host || redact(host) !== host || SECRET_SHAPE_RE.test(host)) return undefined;

    const normalizedPath = normalizedUrlPath(parsed.pathname);
    if (SENSITIVE_URL_PATH_RE.test(normalizedPath)) return parsed.origin;
    if (
      SECRET_URL_PATH_VALUE_RE.test(normalizedPath) ||
      SECRET_SHAPE_RE.test(normalizedPath)
    ) {
      return undefined;
    }

    const pathname = shortUrlPath(parsed.pathname);
    const publicUrl = boundedBrowserUrlDisplay(parsed.origin, pathname);
    if (redact(publicUrl) !== publicUrl || SECRET_SHAPE_RE.test(publicUrl)) return undefined;
    return publicUrl;
  } catch {
    return undefined;
  }
}

function safeBrowserTarget(value?: string): string | undefined {
  const trimmed = value?.trim();
  if (!trimmed) return undefined;

  const publicUrl =
    safeBrowserAbsoluteUrl(trimmed) ??
    safeBrowserAbsoluteUrl(trimmed.match(URL_RE)?.[0]);
  if (publicUrl) return publicUrl;

  const normalized = normalizedUrlPath(trimmed);
  if (
    SENSITIVE_URL_PATH_RE.test(normalized) ||
    SECRET_URL_PATH_VALUE_RE.test(normalized) ||
    SECRET_SHAPE_RE.test(normalized)
  ) {
    return undefined;
  }

  const withoutQuery = normalized.split(/[?#]/, 1)[0]?.trim();
  if (!withoutQuery) return undefined;
  if (PRIVATE_PROGRESS_TEXT_RE.test(withoutQuery) || redact(withoutQuery) !== withoutQuery) {
    return undefined;
  }
  return bounded(withoutQuery, MAX_TARGET_LENGTH);
}

function normalizeTool(label: string): string {
  return label.replace(/[^a-z0-9]/gi, "").toLowerCase();
}

function parsePreviewObject(preview?: string): PreviewObject | null {
  if (!preview) return null;
  try {
    const parsed = JSON.parse(preview);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as PreviewObject;
    }
    if (typeof parsed === "string" && parsed.trim().startsWith("{")) {
      const nested = JSON.parse(parsed);
      if (nested && typeof nested === "object" && !Array.isArray(nested)) {
        return nested as PreviewObject;
      }
    }
  } catch {
    return null;
  }
  return null;
}

function parseRawJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function looksLikeStructuredDataText(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (trimmed.startsWith("{")) {
    return /["'][^"']+["']\s*:/.test(trimmed);
  }
  if (trimmed.startsWith("[")) {
    return /^\[\s*(?:[{\["\]])/.test(trimmed);
  }
  return false;
}

function isRawJsonText(value: string): boolean {
  const trimmed = value.trim();
  if (!looksLikeStructuredDataText(trimmed)) return false;
  const parsed = parseRawJson(trimmed);
  return Boolean(parsed && typeof parsed === "object") || looksLikeStructuredDataText(trimmed);
}

function stringValue(object: PreviewObject | null, keys: string[]): string | undefined {
  if (!object) return undefined;
  for (const key of keys) {
    const value = object[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return undefined;
}

function displayValue(object: PreviewObject | null, keys: string[]): string | undefined {
  if (!object) return undefined;
  for (const key of keys) {
    const value = object[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
  }
  return undefined;
}

function objectValue(object: PreviewObject | null, keys: string[]): PreviewObject | undefined {
  if (!object) return undefined;
  for (const key of keys) {
    const value = object[key];
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return value as PreviewObject;
    }
  }
  return undefined;
}

function firstPreviewText(input: PublicToolPreviewInput): string | undefined {
  return input.outputPreview || input.inputPreview;
}

function snippetFrom(value?: string): string | undefined {
  if (!value) return undefined;
  if (isRawJsonText(value)) return undefined;
  const clean = bounded(value, MAX_SNIPPET_LENGTH);
  return clean || undefined;
}

function commandOutputSnippet(value?: string, language?: ChatResponseLanguage): string | undefined {
  if (!value) return undefined;
  if (/permission denied|requires explicit approval|not allowed/i.test(value)) {
    return localized(language, "Needs permission to continue", "계속하려면 권한이 필요합니다");
  }

  const parsed = parsePreviewObject(value);
  const stdout = stringValue(parsed, ["stdout", "output"]);
  const stderr = stringValue(parsed, ["stderr", "error"]);
  return snippetFrom(stdout ?? stderr ?? value);
}

function pathFrom(object: PreviewObject | null): string | undefined {
  return stringValue(object, [
    "path",
    "file_path",
    "filepath",
    "file",
    "filename",
    "workspacePath",
    "workspace_path",
    "target",
  ]);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function stringFromJsonLikeText(value: string | undefined, keys: string[]): string | undefined {
  if (!value || !looksLikeStructuredDataText(value)) return undefined;
  for (const key of keys) {
    const pattern = new RegExp(
      `["']${escapeRegExp(key)}["']\\s*:\\s*["']([^"']{1,${MAX_TARGET_LENGTH * 2}})`,
      "i",
    );
    const match = value.match(pattern);
    if (match?.[1]?.trim()) {
      return match[1].replace(/\\n/g, "\n").replace(/\\"/g, '"').trim();
    }
    const prefix = stringPrefixFromJsonLikeText(value, key);
    if (prefix) return prefix;
  }
  return undefined;
}

function stringPrefixFromJsonLikeText(value: string, key: string): string | undefined {
  const pattern = new RegExp(`["']${escapeRegExp(key)}["']\\s*:\\s*(["'])`, "i");
  const match = value.match(pattern);
  if (!match || match.index === undefined) return undefined;
  const quote = match[1];
  if (!quote) return undefined;

  let result = "";
  let escaped = false;
  const start = match.index + match[0].length;
  for (let i = start; i < value.length && result.length < MAX_TARGET_LENGTH * 2; i += 1) {
    const ch = value[i];
    if (escaped) {
      if (ch === "n") result += "\n";
      else if (ch === "r") result += "\r";
      else if (ch === "t") result += "\t";
      else result += ch;
      escaped = false;
      continue;
    }
    if (ch === "\\") {
      escaped = true;
      continue;
    }
    if (ch === quote) break;
    result += ch;
  }

  return result.trim() || undefined;
}

function pathFromPreviewText(preview?: string): string | undefined {
  const parsed = previewObject(preview);
  return (
    pathFrom(parsed) ??
    stringFromJsonLikeText(preview, [
      "path",
      "file_path",
      "filepath",
      "file",
      "filename",
      "workspacePath",
      "workspace_path",
      "target",
    ])
  );
}

function fileKind(path?: string, language?: ChatResponseLanguage): string {
  const value = (path ?? "").split(/[?#]/, 1)[0]?.toLowerCase() ?? "";
  if (/\.(tsx|ts|jsx|js|css|scss|html|py|rb|go|rs|java|kt|swift)$/.test(value)) {
    return localized(language, "code file", "코드 파일");
  }
  if (value.endsWith(".pdf")) return localized(language, "PDF document", "PDF 문서");
  if (value.endsWith(".docx") || value.endsWith(".doc")) return localized(language, "Word document", "Word 문서");
  if (value.endsWith(".xlsx") || value.endsWith(".xls") || value.endsWith(".csv")) {
    return localized(language, "spreadsheet", "스프레드시트");
  }
  if (value.endsWith(".md") || value.endsWith(".txt") || value.endsWith(".rst")) {
    return localized(language, "document", "문서");
  }
  if (value.endsWith(".json") || value.endsWith(".yaml") || value.endsWith(".yml")) {
    return localized(language, "data file", "데이터 파일");
  }
  return localized(language, "file", "파일");
}

function unquote(value: string): string {
  return value.trim().replace(/^["']|["']$/g, "");
}

function outputPathFromCommand(command: string): string | undefined {
  const outputFlag = command.match(/(?:^|\s)(?:-o|--output(?:=|\s+))\s*("[^"]+"|'[^']+'|[^\s]+)/i);
  if (outputFlag?.[1]) return unquote(outputFlag[1]);

  const redirect = command.match(/(?:^|\s)>\s*("[^"]+"|'[^']+'|[^\s]+)/);
  if (redirect?.[1]) return unquote(redirect[1]);

  return undefined;
}

function commandPreview(
  command: string,
  outputSnippet?: string,
  language?: ChatResponseLanguage,
): PublicToolPreview {
  const normalized = command.trim();
  const lower = normalized.toLowerCase();
  const outputPath = outputPathFromCommand(normalized);

  if (/^cat\s+/.test(lower) && outputPath) {
    return {
      action: localized(language, "Combining document sections", "문서 섹션 병합"),
      target: bounded(outputPath, MAX_TARGET_LENGTH),
      ...(outputSnippet ? { snippet: outputSnippet } : {}),
    };
  }

  if (/\b(pandoc|libreoffice|soffice|wkhtmltopdf|weasyprint|markdown-pdf)\b/.test(lower)) {
    const target = outputPath ? bounded(outputPath, MAX_TARGET_LENGTH) : undefined;
    return {
      action: isKorean(language) ? `${fileKind(outputPath, language)} 생성` : `Creating ${fileKind(outputPath, language)}`,
      ...(target ? { target } : {}),
      ...(outputSnippet ? { snippet: outputSnippet } : {}),
    };
  }

  if (/\b(vitest|jest|playwright|npm\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test|yarn\s+test)\b/.test(lower)) {
    return {
      action: localized(language, "Checking the work", "작업 확인"),
      target: localized(language, "Running tests", "테스트 실행 중"),
      ...(outputSnippet ? { snippet: outputSnippet } : {}),
    };
  }

  if (/\b(eslint|npm\s+run\s+lint|pnpm\s+run\s+lint|yarn\s+lint)\b/.test(lower)) {
    return {
      action: localized(language, "Checking quality", "품질 확인"),
      target: localized(language, "Running lint", "린트 실행 중"),
      ...(outputSnippet ? { snippet: outputSnippet } : {}),
    };
  }

  if (/\b(next\s+build|npm\s+run\s+build|pnpm\s+run\s+build|yarn\s+build)\b/.test(lower)) {
    return {
      action: localized(language, "Preparing app build", "앱 빌드 준비"),
      target: localized(language, "Building the app", "앱 빌드 중"),
      ...(outputSnippet ? { snippet: outputSnippet } : {}),
    };
  }

  if (/\b(curl|wget|http)\b/.test(lower)) {
    return {
      action: localized(language, "Fetching information", "정보 가져오는 중"),
      target: localized(language, "Running network request", "네트워크 요청 실행 중"),
      ...(outputSnippet ? { snippet: outputSnippet } : {}),
    };
  }

  if (/\b(mkdir|cp|mv|rm|rsync)\b/.test(lower)) {
    return {
      action: localized(language, "Organizing files", "파일 정리 중"),
      target: localized(language, "Updating workspace files", "워크스페이스 파일 업데이트 중"),
      ...(outputSnippet ? { snippet: outputSnippet } : {}),
    };
  }

  const briefTarget = extractBriefTarget(normalized);
  return {
    action: localized(language, "Working", "작업 중"),
    target: briefTarget ?? localized(language, "Processing", "처리 중"),
    ...(outputSnippet ? { snippet: outputSnippet } : {}),
  };
}

function extractBriefTarget(inputPreview?: string): string | undefined {
  if (!inputPreview) return undefined;
  try {
    const parsed = JSON.parse(inputPreview) as Record<string, unknown>;
    const candidate =
      parsed.path ?? parsed.command ?? parsed.query ?? parsed.url ??
      parsed.prompt ?? parsed.file ?? parsed.name ?? parsed.description;
    if (typeof candidate === "string" && candidate.trim()) {
      const trimmed = candidate.trim();
      return trimmed.length > 80 ? trimmed.slice(0, 80) + "…" : trimmed;
    }
  } catch { /* not JSON, try raw */ }
  const trimmed = inputPreview.trim();
  if (trimmed.length > 80) return trimmed.slice(0, 80) + "…";
  return trimmed || undefined;
}

function cleanPromptLine(line: string): string {
  return line
    .trim()
    .replace(/^#+\s*/, "")
    .replace(/\*\*/g, "")
    .replace(/^[-*]\s*/, "")
    .trim();
}

function promptSummary(prompt?: string): string | undefined {
  if (!prompt) return undefined;
  const lines = prompt
    .split(/\r?\n/)
    .map(cleanPromptLine)
    .filter(Boolean);
  const taskLine = lines.find((line) =>
    /^(task|request|work order|작업|요청)\s*:/i.test(line),
  );
  const goalLine = lines.find((line) => /^(goal|objective|목표)\s*:/i.test(line));
  const firstLine = lines[0];
  const title = taskLine ?? (
    firstLine && /^you are\b/i.test(firstLine) ? goalLine : firstLine
  );
  if (!title) return undefined;
  const objective = goalLine && goalLine !== title ? goalLine : undefined;
  return snippetFrom([title, objective].filter(Boolean).join("\n"));
}

function labeledLine(text: string, label: string): string | undefined {
  const pattern = new RegExp(`^${label}\\s*:\\s*(.+)$`, "im");
  const match = text.match(pattern);
  return match?.[1]?.trim() || undefined;
}

function humanizeHelperFinalText(
  text: string,
  language?: ChatResponseLanguage,
): string | undefined {
  const result = labeledLine(text, "RESULT");
  const reasoning = labeledLine(text, "REASONING");
  if (result || reasoning) {
    return snippetFrom(
      [
        result ? `${localized(language, "Result", "결과")}: ${result}` : undefined,
        reasoning ? `${localized(language, "Reason", "이유")}: ${reasoning}` : undefined,
      ]
        .filter(Boolean)
        .join("\n"),
    );
  }

  const cleaned = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !/^MODEL\s*:/i.test(line))
    .join("\n");
  return snippetFrom(cleaned);
}

function helperResultPreview(
  outputPreview?: string,
  language?: ChatResponseLanguage,
): Partial<Pick<PublicToolPreview, "action" | "snippet">> {
  const parsed = parsePreviewObject(outputPreview);
  if (!parsed) {
    const snippet = snippetFrom(outputPreview);
    return snippet ? { snippet } : {};
  }

  const status = displayValue(parsed, ["status"])?.toLowerCase();
  const finalText = displayValue(parsed, [
    "finalText",
    "final_text",
    "summary",
    "result",
    "message",
  ]);
  const errorText = displayValue(parsed, ["error", "stderr"]);

  if (finalText) {
    const snippet = humanizeHelperFinalText(finalText, language);
    return {
      action: status && /abort|cancel|error|fail/.test(status)
        ? localized(language, "Helper stopped", "도우미 중단")
        : localized(language, "Helper reported result", "도우미 결과"),
      ...(snippet ? { snippet } : {}),
    };
  }

  if (errorText) {
    const snippet = snippetFrom(errorText);
    return {
      action: localized(language, "Helper stopped", "도우미 중단"),
      ...(snippet ? { snippet } : {}),
    };
  }

  if (status && /abort|cancel|error|fail/.test(status)) {
    return { action: localized(language, "Helper stopped", "도우미 중단") };
  }

  if (status === "ok" || status === "done" || status === "success") {
    return { action: localized(language, "Helper finished", "도우미 완료") };
  }

  return {};
}

function calculationAction(operation?: string, language?: ChatResponseLanguage): string {
  switch (operation) {
    case "sum":
      return localized(language, "Calculated total", "합계 계산 완료");
    case "average":
      return localized(language, "Calculated average", "평균 계산 완료");
    case "count":
      return localized(language, "Counted items", "항목 수 계산 완료");
    case "min":
      return localized(language, "Found minimum", "최솟값 확인");
    case "max":
      return localized(language, "Found maximum", "최댓값 확인");
    case "percent_change":
      return localized(language, "Calculated change", "변화율 계산 완료");
    case "group_by_sum":
      return localized(language, "Calculated grouped totals", "그룹별 합계 계산 완료");
    default:
      return localized(language, "Calculated result", "계산 완료");
  }
}

function resultText(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const lines = Object.entries(value as Record<string, unknown>)
      .slice(0, 6)
      .map(([key, entryValue]) => `${key}: ${structuredValueText(entryValue)}`)
      .filter((line) => !line.endsWith(": "));
    if (lines.length === 0) return undefined;
    return `Results:\n${lines.join("\n")}`;
  }
  return undefined;
}

function calculationPreview(
  outputPreview?: string,
  language?: ChatResponseLanguage,
): PublicToolPreview | null {
  const parsed = parsePreviewObject(outputPreview);
  const output = objectValue(parsed, ["output"]) ?? parsed;
  if (!output) return null;

  const operation = displayValue(output, ["operation"]);
  if (!operation && !Object.prototype.hasOwnProperty.call(output, "result")) return null;

  const rowCount = displayValue(output, ["rowCount", "rows"]);
  const ignoredCount = displayValue(output, ["ignoredCount"]);
  const result = resultText(output.result);
  const snippet = snippetFrom(
    [
      result
        ? (result.startsWith("Results:")
          ? result
          : `${localized(language, "Result", "결과")}: ${result}`)
        : undefined,
      ignoredCount && ignoredCount !== "0"
        ? `${localized(language, "Ignored", "제외")}: ${ignoredCount}`
        : undefined,
    ]
      .filter(Boolean)
      .join("\n"),
  );

  return {
    action: calculationAction(operation, language),
    ...(rowCount ? { target: localized(language, `${rowCount} rows checked`, `${rowCount}행 확인`) } : {}),
    ...(snippet ? { snippet } : {}),
  };
}

function previewObject(preview?: string): PreviewObject | null {
  const parsed = parsePreviewObject(preview);
  return objectValue(parsed, ["output"]) ?? parsed;
}

function baseName(value?: string): string | undefined {
  if (!value) return undefined;
  const clean = value.split(/[?#]/, 1)[0];
  return clean.split(/[\\/]/).filter(Boolean).pop() ?? clean;
}

function structuredKeyName(key: string): string {
  if (key === "workspacePath" || key === "workspace_path") return "Path";
  return key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function isInternalStructuredKey(key: string): boolean {
  return INTERNAL_STRUCTURED_KEYS.has(key.replace(/[_-]/g, "").toLowerCase());
}

function structuredValueText(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) {
    const trimmed = value.trim();
    if (isRawJsonText(trimmed)) {
      const parsed = parseRawJson(trimmed);
      return structuredValueText(parsed);
    }
    return trimmed;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const count = value.length;
    return `${count} ${count === 1 ? "item" : "items"}`;
  }
  if (value && typeof value === "object") {
    const scalarEntries = Object.entries(value as Record<string, unknown>)
      .filter(([key]) => !isInternalStructuredKey(key))
      .map(([key, entryValue]) => {
        const text = structuredValueText(entryValue);
        return text ? `${key}: ${text}` : undefined;
      })
      .filter(Boolean);
    if (scalarEntries.length > 0) return scalarEntries.slice(0, 3).join(", ");
  }
  return undefined;
}

function browserPreview(
  input: PublicToolPreviewInput,
  language?: ChatResponseLanguage,
): PublicToolPreview | null {
  const output = previewObject(input.outputPreview) ?? previewObject(input.inputPreview);
  if (!output) return null;

  const action = displayValue(output, ["action", "type"]);
  const status = displayValue(output, ["status"]);
  const error = displayValue(output, ["error", "message"]);
  const path = safeBrowserTarget(displayValue(output, ["path", "filename", "title", "url"]));
  const errorSnippet = safeProgressDetail(error);

  if (status && /error|fail|aborted/i.test(status)) {
    return {
      action: localized(language, "Browser step failed", "브라우저 단계 실패"),
      ...(path ? { target: bounded(path, MAX_TARGET_LENGTH) } : {}),
      ...(errorSnippet ? { snippet: errorSnippet } : {}),
    };
  }

  if (action === "create_session" || action === "open_session" || action === "session") {
    return {
      action: localized(language, "Opening browser", "브라우저 여는 중"),
      target: localized(language, "Starting browser session", "브라우저 세션 시작 중"),
    };
  }

  if (action === "scrape" || action === "read" || action === "extract") {
    return {
      action: localized(language, "Reading page", "페이지 읽는 중"),
      ...(path ? { target: bounded(path, MAX_TARGET_LENGTH) } : {}),
      ...(errorSnippet ? { snippet: errorSnippet } : {}),
    };
  }

  if (action === "navigate" || action === "goto" || action === "open") {
    return {
      action: localized(language, "Opening page", "페이지 여는 중"),
      ...(path ? { target: bounded(path, MAX_TARGET_LENGTH) } : {}),
    };
  }

  return {
    action: localized(language, "Using browser", "브라우저 사용 중"),
    ...(path ? { target: bounded(path, MAX_TARGET_LENGTH) } : {}),
  };
}

function browserFallbackPreview(
  previewText?: string,
  language?: ChatResponseLanguage,
): PublicToolPreview {
  const action = stringFromJsonLikeText(previewText, ["action", "type"]);
  const path = safeBrowserTarget(
    stringFromJsonLikeText(previewText, ["path", "filename", "title", "url"]),
  );

  if (action === "create_session" || action === "open_session" || action === "session") {
    return {
      action: localized(language, "Opening browser", "브라우저 여는 중"),
      target: localized(language, "Starting browser session", "브라우저 세션 시작 중"),
    };
  }

  if (action === "scrape" || action === "read" || action === "extract") {
    return {
      action: localized(language, "Reading page", "페이지 읽는 중"),
      ...(path ? { target: bounded(path, MAX_TARGET_LENGTH) } : {}),
    };
  }

  if (action === "navigate" || action === "goto" || action === "open") {
    return {
      action: localized(language, "Opening page", "페이지 여는 중"),
      ...(path ? { target: bounded(path, MAX_TARGET_LENGTH) } : {}),
    };
  }

  return {
    action: localized(language, "Using browser", "브라우저 사용 중"),
    target: localized(language, "Processing browser step", "브라우저 단계 처리 중"),
  };
}

function structuredTarget(object: PreviewObject | null): string | undefined {
  if (!object) return undefined;
  const meta = objectValue(object, ["meta", "artifact"]);
  const directTitle = displayValue(object, ["title", "name", "filename", "query"]);
  const metaTitle = displayValue(meta ?? null, ["title", "name", "filename"]);
  const path = displayValue(object, [
    "workspacePath",
    "workspace_path",
    "path",
    "filePath",
    "file_path",
    "url",
  ]);
  return directTitle ?? metaTitle ?? baseName(path) ?? undefined;
}

function structuredSnippet(object: PreviewObject | null, keys: string[]): string | undefined {
  if (!object) return undefined;
  const lines: string[] = [];
  for (const key of keys) {
    if (isInternalStructuredKey(key)) continue;
    const value = object[key];
    const text = structuredValueText(value);
    if (!text) continue;
    lines.push(`${structuredKeyName(key)}: ${text}`);
  }
  return snippetFrom(lines.join("\n"));
}

function clockPreview(outputPreview?: string): PublicToolPreview | null {
  const output = previewObject(outputPreview);
  if (!output) return null;

  const timezone = displayValue(output, ["timezone"]);
  const localDate = displayValue(output, ["localDate", "date"]);
  const localTime = displayValue(output, ["localTime", "time"]);
  const iso = displayValue(output, ["iso"]);
  const snippet = snippetFrom([localDate, localTime].filter(Boolean).join(" ") || iso);

  if (!timezone && !snippet) return null;
  return {
    action: "Checked current time",
    ...(timezone ? { target: bounded(timezone, MAX_TARGET_LENGTH) } : {}),
    ...(snippet ? { snippet } : {}),
  };
}

function dateRangePreview(outputPreview?: string): PublicToolPreview | null {
  const output = previewObject(outputPreview);
  if (!output) return null;

  const startDate = displayValue(output, ["startDate", "start"]);
  const endDate = displayValue(output, ["endDate", "end"]);
  const dayCount = displayValue(output, ["dayCount", "days"]);
  const timezone = displayValue(output, ["timezone"]);
  if (!startDate && !endDate && !dayCount) return null;

  return {
    action: "Calculated date range",
    ...(startDate && endDate
      ? { target: bounded(`${startDate} to ${endDate}`, MAX_TARGET_LENGTH) }
      : {}),
    ...(dayCount || timezone
      ? {
          snippet: snippetFrom(
            [dayCount ? `${dayCount} days` : undefined, timezone]
              .filter(Boolean)
              .join(" · "),
          ),
        }
      : {}),
  };
}

function modelProgressPreview(
  inputPreview?: string,
  outputPreview?: string,
  language?: ChatResponseLanguage,
): PublicToolPreview {
  const input = previewObject(inputPreview);
  const output = outputPreview ? bounded(outputPreview, MAX_SNIPPET_LENGTH) : undefined;
  const stage = displayValue(input, ["stage"]);
  const label = displayValue(input, ["label"]);
  const detail = displayValue(input, ["detail"]);
  const elapsedMs = displayValue(input, ["elapsedMs"]);
  const elapsedSeconds = elapsedMs ? Math.max(1, Math.round(Number(elapsedMs) / 1000)) : null;
  const elapsed = elapsedSeconds
    ? (isKorean(language) ? `${elapsedSeconds}초째 작업 중` : `${elapsedSeconds}s elapsed`)
    : undefined;
  const isHeartbeat = stage === "heartbeat";
  const heartbeatLabel = isHeartbeat && label && !/^(still working|계속 작업 중)$/iu.test(label.trim())
    ? bounded(label, MAX_TARGET_LENGTH)
    : undefined;

  const action = stage === "completed"
    ? localized(language, "Model step finished", "모델 단계 완료")
    : isHeartbeat
      ? heartbeatLabel ?? localized(language, "Still working", "계속 작업 중")
    : localized(language, "Thinking through next step", "다음 단계 판단 중");
  const target = isHeartbeat
    ? elapsed
    : label && !/thinking through next step/i.test(label)
    ? bounded(label, MAX_TARGET_LENGTH)
    : elapsed;
  const snippet = snippetFrom([detail, output].filter(Boolean).join("\n"));

  return {
    action,
    ...(target ? { target } : {}),
    ...(snippet ? { snippet } : {}),
  };
}

function activityProgressPreview(
  inputPreview?: string,
  outputPreview?: string,
  language?: ChatResponseLanguage,
): PublicToolPreview {
  const input = previewObject(inputPreview);
  const label = displayValue(input, ["label"]) ?? localized(language, "Working through current step", "작업 진행 중");
  const target = displayValue(input, ["target"]);
  const detail = displayValue(input, ["detail"]);
  const elapsedMs = displayValue(input, ["elapsedMs"]);
  const elapsedSeconds = elapsedMs ? Math.max(1, Math.round(Number(elapsedMs) / 1000)) : null;
  const elapsed = elapsedSeconds
    ? (isKorean(language) ? `${elapsedSeconds}초째 작업 중` : `${elapsedSeconds}s elapsed`)
    : undefined;
  const snippet = snippetFrom([target, detail].filter(Boolean).join("\n"));

  return {
    action: bounded(label, MAX_TARGET_LENGTH),
    ...(elapsed ? { target: elapsed } : {}),
    ...(snippet ? { snippet } : {}),
  };
}

function generatedOutputAction(tool: string): string | undefined {
  switch (tool) {
    case "documentwrite":
      return "Created document";
    case "spreadsheetwrite":
      return "Created spreadsheet";
    case "filedeliver":
    case "filesend":
      return "Prepared file";
    case "artifactcreate":
      return "Created artifact";
    case "artifactread":
      return "Read artifact";
    case "artifactupdate":
      return "Updated artifact";
    case "artifactdelete":
      return "Deleted artifact";
    case "artifactlist":
      return "Listed artifacts";
    default:
      return undefined;
  }
}

function generatedOutputPreview(
  tool: string,
  input: PublicToolPreviewInput,
): PublicToolPreview | null {
  const action = generatedOutputAction(tool);
  if (!action) return null;

  const output = previewObject(input.outputPreview) ?? previewObject(input.inputPreview);
  if (!output) return null;

  const meta = objectValue(output, ["meta", "artifact"]);
  const path = displayValue(output, [
    "workspacePath",
    "workspace_path",
    "path",
    "filePath",
    "file_path",
    "url",
  ]);
  const title =
    displayValue(output, ["filename", "name", "title"]) ??
    displayValue(meta ?? null, ["title", "filename", "name"]) ??
    baseName(path);
  const kind =
    displayValue(meta ?? null, ["kind", "type"]) ?? displayValue(output, ["kind", "type"]);
  const content = stringValue(output, ["content", "text", "summary", "message"]);
  const snippet = snippetFrom(
    tool === "artifactread"
      ? content
      : path && title !== path
        ? path
        : kind,
  );

  return {
    action,
    ...(title ? { target: bounded(title, MAX_TARGET_LENGTH) } : {}),
    ...(snippet ? { snippet } : {}),
  };
}

function firstUrlFromValue(value: unknown, depth: number): string | undefined {
  if (depth > 8 || value === null || value === undefined) return undefined;
  if (typeof value === "string") return safePublicUrlFromText(value);
  if (Array.isArray(value)) {
    for (const item of value) {
      const url = firstUrlFromValue(item, depth + 1);
      if (url) return url;
    }
    return undefined;
  }
  if (typeof value === "object") {
    return firstUrlFromResults(value as PreviewObject, depth + 1);
  }
  return undefined;
}

function firstUrlFromResults(object: PreviewObject | null, depth = 0): string | undefined {
  if (!object || depth > 8) return undefined;

  for (const key of URL_VALUE_KEYS) {
    const url = safePublicUrl(displayValue(object, [key]));
    if (url) return url;
  }

  for (const key of URL_TEXT_KEYS) {
    const url = safePublicUrlFromText(displayValue(object, [key]));
    if (url) return url;
  }

  for (const key of URL_CONTAINER_KEYS) {
    const url = firstUrlFromValue(object[key], depth + 1);
    if (url) return url;
  }

  return undefined;
}

function searchPreview(tool: string, input: PublicToolPreviewInput): PublicToolPreview | null {
  const isWebSearch = tool === "websearch" || tool === "websearchtool" ||
    tool === "searchingtheweb" || tool === "searchweb" || tool === "websearching";
  const isKnowledgeSearch = tool === "knowledgesearch" || tool === "knowledgesearchtool" ||
    tool === "searchingknowledgebase";
  if (!isWebSearch && !isKnowledgeSearch) return null;

  const parsedInput = previewObject(input.inputPreview);
  const parsedOutput = previewObject(input.outputPreview);
  const query = safeProgressDetail(
    displayValue(parsedInput, ["query", "q", "search"]) ??
      displayValue(parsedOutput, ["query", "q", "search"]),
  );
  const resultUrl = isWebSearch
    ? firstUrlFromResults(parsedOutput) ??
      firstUrlFromResults(parsedInput) ??
      safePublicUrlFromText(input.outputPreview) ??
      safePublicUrlFromText(input.inputPreview)
    : undefined;
  const target = resultUrl ?? query;
  const resultCount =
    displayValue(parsedOutput, ["count", "resultCount", "total"]) ??
    (Array.isArray(parsedOutput?.results)
      ? String(parsedOutput.results.length)
      : undefined);
  const safeDetail = safeProgressDetail(
    displayValue(parsedOutput, ["detail", "summary", "message"]),
  );
  const snippet = snippetFrom(
    [
      query && resultUrl ? `Query: ${query}` : undefined,
      resultUrl ? `URL: ${resultUrl}` : undefined,
      safeDetail && safeDetail !== resultUrl ? `Detail: ${safeDetail}` : undefined,
      resultCount ? `${resultCount} result${resultCount === "1" ? "" : "s"}` : undefined,
    ]
      .filter(Boolean)
      .join("\n"),
  );

  return {
    action: isWebSearch ? "Searching the web" : "Searching knowledge base",
    ...(target ? { target: bounded(target, MAX_TARGET_LENGTH) } : {}),
    ...(snippet ? { snippet } : {}),
  };
}

function webFetchPreview(input: PublicToolPreviewInput): PublicToolPreview | null {
  const parsedInput = previewObject(input.inputPreview);
  const parsedOutput = previewObject(input.outputPreview);
  const url =
    safePublicUrl(displayValue(parsedInput, ["url", "uri", "sourceUrl", "source_url"])) ??
    firstUrlFromResults(parsedOutput);
  const title = displayValue(parsedOutput, ["title", "name"]);
  const summary = displayValue(parsedOutput, ["summary", "message"]);
  const snippet = snippetFrom([title, summary].filter(Boolean).join("\n"));
  if (!url && !snippet) return null;
  return {
    action: "Reading web page",
    ...(url ? { target: bounded(url, MAX_TARGET_LENGTH) } : {}),
    ...(snippet ? { snippet } : {}),
  };
}

function taskBoardPreview(input: PublicToolPreviewInput): PublicToolPreview | null {
  const output = previewObject(input.outputPreview) ?? previewObject(input.inputPreview);
  const tasks = Array.isArray(output?.tasks) ? output.tasks : null;
  if (!tasks) return null;

  const taskObjects = tasks.filter(
    (task): task is PreviewObject =>
      Boolean(task) && typeof task === "object" && !Array.isArray(task),
  );
  const completed = taskObjects.filter((task) =>
    /^(completed|done)$/i.test(displayValue(task, ["status"]) ?? ""),
  ).length;
  const current = taskObjects.find((task) =>
    /^(in_progress|running)$/i.test(displayValue(task, ["status"]) ?? ""),
  );
  const currentTitle = displayValue(current ?? null, ["title", "name", "description"]);

  return {
    action: "Updated task list",
    target: `${completed}/${taskObjects.length} tasks complete`,
    ...(currentTitle
      ? { snippet: `Now: ${bounded(currentTitle, MAX_SNIPPET_LENGTH - 5)}` }
      : {}),
  };
}

function structuredJsonPreview(
  label: string,
  inputPreview?: string,
  outputPreview?: string,
): PublicToolPreview | null {
  const object = previewObject(outputPreview) ?? previewObject(inputPreview);
  if (!object) return null;

  const target = structuredTarget(object);
  const snippet = structuredSnippet(object, [
    "status",
    "message",
    "summary",
    "result",
    "count",
    "rowCount",
    "dayCount",
    "workspacePath",
    "path",
    "query",
  ]);

  if (!target && !snippet) return null;
  return {
    action: label,
    ...(target ? { target: bounded(target, MAX_TARGET_LENGTH) } : {}),
    ...(snippet ? { snippet } : {}),
  };
}

function structuredFallbackPreview(
  label: string,
  inputPreview?: string,
  outputPreview?: string,
  language?: ChatResponseLanguage,
): PublicToolPreview {
  const tool = normalizeTool(label);
  const previewText = outputPreview || inputPreview;
  const path = pathFromPreviewText(outputPreview) ?? pathFromPreviewText(inputPreview);

  if (tool === "browser" || tool === "browseruse" || tool === "browserworker") {
    return browserFallbackPreview(previewText, language);
  }

  if (tool === "taskget" || tool === "taskread" || tool === "taskstatus") {
    return {
      action: localized(language, "Checking helper progress", "도우미 진행 확인 중"),
      target: localized(language, "Waiting for helper update", "도우미 업데이트 대기 중"),
    };
  }

  if (path) {
    return {
      action: isKorean(language) ? `${fileKind(path, language)} 검토` : `Reviewing ${fileKind(path, language)}`,
      target: bounded(path, MAX_TARGET_LENGTH),
    };
  }

  if (tool === "codeworkspace" || tool === "workspace" || tool === "workspacecode") {
    return {
      action: localized(language, "Working in workspace", "워크스페이스 작업 중"),
      target: localized(language, "Processing workspace step", "워크스페이스 단계 처리 중"),
    };
  }

  return {
    action: label,
    target: localized(language, "Processing tool result", "도구 결과 처리 중"),
  };
}

export function derivePublicToolPreview(
  input: PublicToolPreviewInput,
): PublicToolPreview | null {
  const tool = normalizeTool(input.label);
  const language = input.language;
  const parsedInput = parsePreviewObject(input.inputPreview);
  const parsedOutput = parsePreviewObject(input.outputPreview);
  const targetPath =
    pathFrom(parsedInput) ??
    pathFrom(parsedOutput) ??
    pathFromPreviewText(input.inputPreview) ??
    pathFromPreviewText(input.outputPreview);
  const outputSnippet = snippetFrom(input.outputPreview);

  if (tool === "fileread" || tool === "read") {
    return {
      action: isKorean(language) ? `${fileKind(targetPath, language)} 검토` : `Reviewing ${fileKind(targetPath, language)}`,
      ...(targetPath ? { target: bounded(targetPath, MAX_TARGET_LENGTH) } : {}),
      ...(outputSnippet ? { snippet: outputSnippet } : {}),
    };
  }

  if (tool === "filewrite" || tool === "write") {
    const content = stringValue(parsedInput, ["content", "text", "body"]);
    const contentSnippet = snippetFrom(content);
    return {
      action: isKorean(language) ? `${fileKind(targetPath, language)} 생성` : `Creating ${fileKind(targetPath, language)}`,
      ...(targetPath ? { target: bounded(targetPath, MAX_TARGET_LENGTH) } : {}),
      ...(contentSnippet
        ? { snippet: contentSnippet }
        : outputSnippet
          ? { snippet: outputSnippet }
          : {}),
    };
  }

  if (tool === "fileedit" || tool === "edit") {
    const oldText = snippetFrom(stringValue(parsedInput, ["old_string", "oldText", "old"]));
    const newText = snippetFrom(stringValue(parsedInput, ["new_string", "newText", "replacement"]));
    const editSnippet =
      oldText && newText
        ? snippetFrom(`${localized(language, "Replace", "교체")}: ${oldText} -> ${newText}`)
        : snippetFrom(firstPreviewText(input));
    return {
      action: isKorean(language) ? `${fileKind(targetPath, language)} 수정` : `Updating ${fileKind(targetPath, language)}`,
      ...(targetPath ? { target: bounded(targetPath, MAX_TARGET_LENGTH) } : {}),
      ...(editSnippet ? { snippet: editSnippet } : {}),
    };
  }

  if (tool === "bash" || tool === "execcommand" || tool === "shell") {
    const command =
      stringValue(parsedInput, ["command", "cmd", "script"]) ?? input.inputPreview;
    if (!command) return null;
    return commandPreview(command, commandOutputSnippet(input.outputPreview, language), language);
  }

  if (tool === "browser" || tool === "browseruse" || tool === "browserworker") {
    const preview = browserPreview(input, language);
    if (preview) return preview;
  }

  if (tool === "spawnagent") {
    const prompt =
      stringValue(parsedInput, ["prompt", "task", "instructions", "message"]) ??
      stringFromJsonLikeText(input.inputPreview, ["prompt", "task", "instructions", "message"]);
    const summary = promptSummary(prompt);
    const resultPreview = helperResultPreview(input.outputPreview, language);
    return {
      action: resultPreview.action ?? localized(language, "Assigning helper", "도우미 배정"),
      ...(summary ? { target: summary } : {}),
      ...(resultPreview.snippet ? { snippet: resultPreview.snippet } : {}),
    };
  }

  if (tool === "calculation") {
    const preview = calculationPreview(input.outputPreview, language);
    if (preview) return preview;
  }

  if (tool === "clock") {
    const preview = clockPreview(input.outputPreview);
    if (preview) return preview;
  }

  if (tool === "daterange") {
    const preview = dateRangePreview(input.outputPreview);
    if (preview) return preview;
  }

  if (tool === "modelprogress") {
    return modelProgressPreview(input.inputPreview, input.outputPreview, language);
  }

  if (tool === "activityprogress") {
    return activityProgressPreview(input.inputPreview, input.outputPreview, language);
  }

  if (tool === "webfetch" || tool === "webfetchtool") {
    const preview = webFetchPreview(input);
    if (preview) return preview;
  }

  if (tool === "taskboard" || tool === "taskupdate") {
    const preview = taskBoardPreview(input);
    if (preview) return preview;
  }

  {
    const preview = generatedOutputPreview(tool, input);
    if (preview) return preview;
  }

  {
    const preview = searchPreview(tool, input);
    if (preview) return preview;
  }

  {
    const preview = structuredJsonPreview(input.label, input.inputPreview, input.outputPreview);
    if (preview) return preview;
  }

  const previewText = firstPreviewText(input);
  const genericSnippet = snippetFrom(previewText);
  if (!genericSnippet) {
    const structuredObject = previewObject(input.outputPreview) ?? previewObject(input.inputPreview);
    if (
      structuredObject ||
      (previewText && looksLikeStructuredDataText(previewText))
    ) {
      return structuredFallbackPreview(input.label, input.inputPreview, input.outputPreview, language);
    }
    return null;
  }
  return {
    action: input.label,
    snippet: genericSnippet,
  };
}
