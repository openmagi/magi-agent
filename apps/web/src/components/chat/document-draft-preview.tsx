"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatResponseLanguage, DocumentDraftPreview } from "@/chat-core";

type DocumentDraftPreviewSurface = "work-console" | "run-inspector";

interface DocumentDraftPreviewCardProps {
  draft: DocumentDraftPreview;
  language?: ChatResponseLanguage;
  surface?: DocumentDraftPreviewSurface;
}

const MAX_DOCUMENT_DRAFT_PREVIEW = 6_000;
const SENSITIVE_TEXT_RE =
  /(authorization|bearer|cookie|credential|private|session[_-]?token|token|secret|api[_-]?key|auth[_-]?token|github_pat_|gh[pousr]_|sk-|[rs]k_(?:live|test)_)/i;
const PRIVATE_PATH_RE =
  /(?:~\/|\.{2}\/|\/(?:Users|home|var|etc|private|tmp|mnt|Volumes|workspace)\/|[A-Za-z]:\\)(?:[^\s"'<>)]*)?/i;
const SENSITIVE_ROUTE_PATH_RE =
  /(?:^|[^A-Za-z0-9._/-])((?:https?:\/\/[^/\s"'<>)]*)?(?:\/?[A-Za-z0-9._-]+\/)*\/?(?:auth|callback|cookie|oauth|sessions|session)(?:[/?#][^\s"'<>)]*|$))/i;
const SENSITIVE_ROUTE_PATH_GLOBAL_RE =
  /(^|[^A-Za-z0-9._/-])((?:https?:\/\/[^/\s"'<>)]*)?(?:\/?[A-Za-z0-9._-]+\/)*\/?(?:auth|callback|cookie|oauth|sessions|session)(?:[/?#][^\s"'<>)]*|$))/gi;
const ROUTE_PATH_TOKEN_RE =
  /(?:https?:\/\/[^\s"'<>)]*|[A-Za-z0-9._/-]*(?:\/|%(?:2f|5c)|[?#]|%(?:3f|23))[^\s"'<>)]*)/gi;
const SENSITIVE_QUERY_FRAGMENT_RE =
  /[?#][^\s"'<>)]*(?:auth|code|cookie|session|state|token)[^\s"'<>)]*/i;
const SENSITIVE_QUERY_FRAGMENT_GLOBAL_RE =
  /[?#][^\s"'<>)]*(?:auth|code|cookie|session|state|token)[^\s"'<>)]*/gi;

function normalizedRouteSeparators(value: string): string {
  return value
    .replace(/%(?:2f|5c)/gi, "/")
    .replace(/%3f/gi, "?")
    .replace(/%23/gi, "#");
}

function hasSensitiveRoutePath(value: string): boolean {
  return (
    SENSITIVE_ROUTE_PATH_RE.test(value) ||
    SENSITIVE_ROUTE_PATH_RE.test(normalizedRouteSeparators(value))
  );
}

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

function documentLabel(draft: DocumentDraftPreview): string {
  if (draft.filename && !hasPrivateText(draft.filename)) return draft.filename;
  switch (draft.format) {
    case "html":
      return "HTML";
    case "md":
      return "Markdown";
    case "txt":
    default:
      return "Text";
  }
}

function hasPrivateText(value: string): boolean {
  return (
    SENSITIVE_TEXT_RE.test(value) ||
    PRIVATE_PATH_RE.test(value) ||
    hasSensitiveRoutePath(value) ||
    SENSITIVE_QUERY_FRAGMENT_RE.test(value)
  );
}

function redactPublicText(value: string): string {
  return value
    .replace(
      /(?:~\/|\.{2}\/|\/(?:Users|home|var|etc|private|tmp|mnt|Volumes|workspace)\/|[A-Za-z]:\\)(?:[^\s"'<>)]*)?/gi,
      "[redacted-path]",
    )
    .replace(ROUTE_PATH_TOKEN_RE, (token) =>
      hasSensitiveRoutePath(token) ? "[redacted-path]" : token,
    )
    .replace(SENSITIVE_ROUTE_PATH_GLOBAL_RE, "$1[redacted-path]")
    .replace(SENSITIVE_QUERY_FRAGMENT_GLOBAL_RE, "[redacted-query]")
    .replace(/(?:Set-)?Cookie:\s*[^\r\n<>]*/gi, (match) => {
      const prefix = match.toLowerCase().startsWith("set-cookie") ? "Set-Cookie" : "Cookie";
      return `${prefix}: [redacted]`;
    })
    .replace(/Authorization:\s*Bearer\s+\S+/gi, "Authorization: Bearer [redacted]")
    .replace(/\b(?:cookie|session|token|session_token|auth_token|api_key)=\S+/gi, (match) => {
      const key = match.split("=", 1)[0] ?? "token";
      return `${key}=[redacted]`;
    })
    .replace(/\bgithub_pat_[A-Za-z0-9_]{12,}\b/g, "[redacted]")
    .replace(/\bgh[pousr]_[A-Za-z0-9_]{20,}\b/g, "[redacted]")
    .replace(/\b[rs]k_(?:live|test)_[A-Za-z0-9_]{8,}\b/g, "[redacted]")
    .replace(/\bsk-[A-Za-z0-9_-]{20,}\b/g, "[redacted]");
}

function previewText(draft: DocumentDraftPreview): string {
  const redacted = redactPublicText(draft.contentPreview);
  const bounded =
    redacted.length > MAX_DOCUMENT_DRAFT_PREVIEW
      ? redacted.slice(0, MAX_DOCUMENT_DRAFT_PREVIEW)
      : redacted;
  return draft.truncated || redacted.length > MAX_DOCUMENT_DRAFT_PREVIEW
    ? `...\n${bounded}`
    : bounded;
}

function previewLength(draft: DocumentDraftPreview): number {
  return Math.min(
    Math.max(0, Math.floor(draft.contentLength)),
    MAX_DOCUMENT_DRAFT_PREVIEW,
  );
}

function sizeLabel(draft: DocumentDraftPreview, language?: ChatResponseLanguage): string {
  const length = previewLength(draft);
  if (isKorean(language)) return `${length.toLocaleString()}자`;
  const unit = length === 1 ? "char" : "chars";
  return `${length.toLocaleString()} ${unit}`;
}

function bodyMaxHeight(surface: DocumentDraftPreviewSurface): string {
  return surface === "work-console" ? "max-h-44" : "max-h-36";
}

function htmlFrameHeight(surface: DocumentDraftPreviewSurface): string {
  return surface === "work-console" ? "h-44" : "h-36";
}

function DocumentDraftPreviewBody({
  draft,
  surface,
}: {
  draft: DocumentDraftPreview;
  surface: DocumentDraftPreviewSurface;
}) {
  if (draft.format === "md") {
    return (
      <div
        className={`${bodyMaxHeight(surface)} prose-chat max-w-none overflow-auto bg-[#FBFBFD] px-2.5 py-2 text-[11px] leading-snug text-secondary/75`}
        data-document-draft-markdown-preview="true"
      >
        <ReactMarkdown remarkPlugins={[[remarkGfm, { singleTilde: false }]]}>
          {previewText(draft)}
        </ReactMarkdown>
      </div>
    );
  }

  if (draft.format === "html") {
    const label = documentLabel(draft);
    return (
      <div
        className={`${bodyMaxHeight(surface)} overflow-hidden bg-white`}
        data-document-draft-html-preview="true"
      >
        <iframe
          title={`${label} document preview`}
          sandbox=""
          srcDoc={previewText(draft)}
          className={`${htmlFrameHeight(surface)} w-full border-0 bg-white`}
        />
      </div>
    );
  }

  return (
    <pre
      className={`${bodyMaxHeight(surface)} overflow-auto bg-[#FBFBFD] px-2.5 py-2 whitespace-pre-wrap break-words text-[11px] leading-snug text-secondary/75`}
      data-document-draft-text-preview="true"
    >
      {previewText(draft)}
    </pre>
  );
}

function DocumentDraftPreviewContents({
  draft,
  language,
  surface,
}: {
  draft: DocumentDraftPreview;
  language?: ChatResponseLanguage;
  surface: DocumentDraftPreviewSurface;
}) {
  return (
    <>
      <div className="flex min-w-0 items-center justify-between gap-2 border-b border-black/[0.06] px-2.5 py-1.5">
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-secondary/45">
          {draft.status === "done"
            ? t(language, "Document written", "문서 작성 완료")
            : t(language, "Writing document", "문서 작성 중")}
        </span>
        <span className="min-w-0 truncate text-[10.5px] text-secondary/55">
          {documentLabel(draft)}
          <span className="text-secondary/35"> · {sizeLabel(draft, language)}</span>
        </span>
      </div>
      <DocumentDraftPreviewBody draft={draft} surface={surface} />
    </>
  );
}

export function DocumentDraftPreviewCard({
  draft,
  language,
  surface = "work-console",
}: DocumentDraftPreviewCardProps) {
  if (surface === "run-inspector") {
    return (
      <div
        className="mt-2 overflow-hidden rounded-lg border border-[var(--color-accent)]/15 bg-white"
        data-run-inspector-document-draft="true"
      >
        <DocumentDraftPreviewContents draft={draft} language={language} surface={surface} />
      </div>
    );
  }

  return (
    <section
      className="mb-3 overflow-hidden rounded-xl border border-[var(--color-accent)]/15 bg-white shadow-[0_1px_6px_rgba(124,58,237,0.08)]"
      data-work-console-document-draft="true"
    >
      <DocumentDraftPreviewContents draft={draft} language={language} surface={surface} />
    </section>
  );
}
