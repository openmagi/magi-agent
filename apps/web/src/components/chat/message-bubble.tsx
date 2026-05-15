"use client";

import { Children, isValidElement, useMemo, useState, useEffect, useCallback, useId } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { TaskBoard } from "./task-board";
import { AgentActivityTimeline } from "./agent-activity-timeline";
import { EChartRenderer } from "./echart-renderer";
import { parseMarkers } from "@/lib/chat/attachment-marker";
import { parseKbContextMarker } from "@/lib/chat/kb-context-marker";
import { buildMessageCopyText } from "@/lib/chat/message-copy";
import { getAttachmentUrl, getKnowledgeDocumentUrl, fetchAttachmentBlob } from "@/lib/chat/attachments";
import { stripAssistantMetadataPreamble } from "@/lib/chat/visible-content";
import type {
  InspectedSource,
  ReplyTo,
  ResearchEvidenceSnapshot,
  ResponseUsage,
  ToolActivity,
  TaskBoardSnapshot,
} from "@/lib/chat/types";

export type MessageContextAction = "copy" | "select" | "reply";

interface MessageBubbleProps {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp?: number;
  isStreaming?: boolean;
  /** Persisted thinking content (shown as collapsible block) */
  thinkingContent?: string;
  thinkingDuration?: number;
  /** Persisted tool/skill activities (shown in the activity timeline). */
  activities?: ToolActivity[];
  /** Persisted / live TaskBoard snapshot rendered above the message body. */
  taskBoard?: TaskBoardSnapshot;
  /** Durable source/citation evidence captured with the finalized assistant message. */
  researchEvidence?: ResearchEvidenceSnapshot;
  /** Token/cost totals for the completed assistant turn. */
  usage?: ResponseUsage;
  botId?: string;
  /** Quoted-reply metadata (if this message is a reply to another) */
  replyTo?: ReplyTo;
  /** #86 — Set on user messages landed mid-turn via /v1/chat/:botId/inject. */
  injected?: boolean;
  /** Selection mode */
  selectionMode?: boolean;
  selected?: boolean;
  onSelect?: () => void;
  onContextAction?: (action: MessageContextAction) => void;
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  const h = d.getHours();
  const m = d.getMinutes().toString().padStart(2, "0");
  const ampm = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 || 12;
  return `${h12}:${m} ${ampm}`;
}

function looksLikeEChartOption(source: string): boolean {
  try {
    const parsed = JSON.parse(source.trim()) as unknown;
    if (!parsed || typeof parsed !== "object") return false;
    const obj = parsed as Record<string, unknown>;
    const option = obj.option && typeof obj.option === "object"
      ? (obj.option as Record<string, unknown>)
      : obj;
    return Array.isArray(option.series) && (
      "xAxis" in option ||
      "yAxis" in option ||
      "radar" in option ||
      "geo" in option ||
      "calendar" in option ||
      "title" in option
    );
  } catch {
    return false;
  }
}

function formatUsageCost(costUsd: number): string {
  if (costUsd <= 0) return "$0.00";
  if (costUsd < 0.0001) return "<$0.0001";
  if (costUsd < 0.01) return `$${costUsd.toFixed(6)}`;
  if (costUsd < 1) return `$${costUsd.toFixed(4)}`;
  return `$${costUsd.toFixed(2)}`;
}

function formatUsageSummary(usage: ResponseUsage): string {
  const input = Math.max(0, Math.floor(usage.inputTokens));
  const output = Math.max(0, Math.floor(usage.outputTokens));
  const total = input + output;
  return `${total.toLocaleString()} tokens · ${input.toLocaleString()} in / ${output.toLocaleString()} out · ${formatUsageCost(usage.costUsd)}`;
}

/** Download a file via auth header, then trigger browser save */
function useAuthDownload() {
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const download = useCallback(async (url: string, filename: string, id?: string) => {
    setDownloadingId(id ?? filename);
    try {
      const blob = await fetchAttachmentBlob(url);
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
    } catch {
      window.open(url, "_blank");
    } finally {
      setDownloadingId(null);
    }
  }, []);

  return { download, downloadingId };
}

/** Image that fetches via auth header, displays as blob URL */
function AuthImage({ url, alt, className }: { url: string; alt: string; className?: string }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    let revoke: string | null = null;
    fetchAttachmentBlob(url)
      .then((blob) => {
        const u = URL.createObjectURL(blob);
        revoke = u;
        setBlobUrl(u);
      })
      .catch(() => {});
    return () => { if (revoke) URL.revokeObjectURL(revoke); };
  }, [url]);

  if (!blobUrl) {
    return <div className={`${className} bg-black/[0.04] animate-pulse`} />;
  }

  return <img src={blobUrl} alt={alt} className={className} loading="lazy" />;
}

interface ImagePreview {
  url: string;
  filename: string;
}

function ExpandableImageAttachment({
  url,
  filename,
  onOpen,
}: {
  url: string;
  filename: string;
  onOpen: (preview: ImagePreview) => void;
}) {
  return (
    <button
      type="button"
      aria-label={`Open image ${filename}`}
      onClick={(e) => {
        e.stopPropagation();
        onOpen({ url, filename });
      }}
      className="group/image relative overflow-hidden rounded-xl border border-black/[0.08] bg-black/[0.03] transition hover:border-black/[0.18] hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-[#7C3AED]/50"
    >
      <AuthImage
        url={url}
        alt={filename}
        className="block max-w-[180px] max-h-[140px] sm:max-w-[240px] sm:max-h-[180px] object-cover transition duration-150 group-hover/image:scale-[1.015]"
      />
      <span className="sr-only">{filename}</span>
      <span
        aria-hidden="true"
        className="pointer-events-none absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full bg-white/90 text-foreground/70 opacity-0 shadow-sm transition group-hover/image:opacity-100 group-focus-visible/image:opacity-100"
      >
        <svg
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="m21 21-4.35-4.35" />
          <circle cx="11" cy="11" r="7" />
        </svg>
      </span>
    </button>
  );
}

function ImageLightbox({
  preview,
  onClose,
}: {
  preview: ImagePreview | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!preview) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [preview, onClose]);

  if (!preview || typeof document === "undefined") return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-black/80 px-4 py-6"
      role="dialog"
      aria-modal="true"
      aria-label={preview.filename}
      onMouseDown={onClose}
    >
      <div
        className="relative flex max-h-full max-w-full flex-col items-center gap-3"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close image preview"
          className="absolute -right-2 -top-2 z-10 flex h-9 w-9 items-center justify-center rounded-full bg-white text-foreground shadow-lg transition hover:bg-white/90 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#7C3AED]/60"
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </button>
        <AuthImage
          url={preview.url}
          alt={preview.filename}
          className="max-h-[82vh] max-w-[94vw] rounded-xl bg-white object-contain shadow-2xl"
        />
        <div className="max-w-[94vw] truncate rounded-full bg-black/55 px-3 py-1 text-xs text-white/90">
          {preview.filename}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function citationEvidenceLabel(evidence: ResearchEvidenceSnapshot): string | null {
  if (!evidence.citationGate) return null;
  switch (evidence.citationGate.verdict) {
    case "ok":
      return "Citation check passed";
    case "violation":
      return "Citation gaps found";
    case "pending":
    default:
      return "Citation check pending";
  }
}

function sourceDisplayName(source: InspectedSource): string {
  if (source.title) return source.title;
  try {
    const url = new URL(source.uri);
    return url.hostname || source.uri;
  } catch {
    return source.uri;
  }
}

const CITATION_RE = /\[src_(\w+)\]/g;

function CitationBadge({
  sourceId,
  source,
}: {
  sourceId: string;
  source: InspectedSource | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const badgeRef = useCallback(
    (node: HTMLElement | null) => {
      if (!node || !open) return;
      const rect = node.getBoundingClientRect();
      setPos({ top: rect.bottom + 4, left: rect.left + rect.width / 2 });
    },
    [open],
  );

  const label = `src_${sourceId}`;

  return (
    <>
      <span
        ref={badgeRef}
        className="citation-badge"
        role="button"
        tabIndex={0}
        aria-label={source ? `Source: ${sourceDisplayName(source)}` : label}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        {label}
      </span>
      {open && source && pos && typeof document !== "undefined" &&
        createPortal(
          <div
            className="citation-tooltip"
            style={{ top: pos.top, left: pos.left }}
          >
            <div className="font-medium truncate max-w-[280px]">{sourceDisplayName(source)}</div>
            <div className="flex items-center gap-1.5 mt-0.5 text-[10px] opacity-70">
              <span className="uppercase tracking-wide">{sourceKindDisplayName(source.kind)}</span>
              <span className="truncate max-w-[200px]">{sourceUriDisplayName(source.uri)}</span>
            </div>
            {source.snippets && source.snippets.length > 0 && (
              <div className="mt-1 pt-1 border-t border-white/10 text-[10px] opacity-60 line-clamp-2">
                {source.snippets[0]}
              </div>
            )}
          </div>,
          document.body,
        )}
    </>
  );
}

function renderWithCitations(
  text: string,
  sources: InspectedSource[],
): ReactNode[] {
  const sourceMap = new Map(sources.map((s) => [s.sourceId, s]));
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  const re = new RegExp(CITATION_RE.source, "g");
  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const id = match[1];
    parts.push(
      <CitationBadge
        key={`${id}-${match.index}`}
        sourceId={id}
        source={sourceMap.get(`src_${id}`)}
      />,
    );
    lastIndex = re.lastIndex;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

function injectCitationsIntoChildren(
  children: ReactNode,
  sources: InspectedSource[],
): ReactNode {
  if (typeof children === "string") {
    if (!CITATION_RE.test(children)) return children;
    CITATION_RE.lastIndex = 0;
    return <>{renderWithCitations(children, sources)}</>;
  }
  if (Array.isArray(children)) {
    return children.map((child, i) => {
      if (typeof child === "string") {
        if (!CITATION_RE.test(child)) return child;
        CITATION_RE.lastIndex = 0;
        return <span key={i}>{renderWithCitations(child, sources)}</span>;
      }
      return child;
    });
  }
  return children;
}

function sourceKindDisplayName(kind: InspectedSource["kind"]): string {
  return kind.replace(/_/g, " ");
}

function sourceUriDisplayName(uri: string): string {
  try {
    const url = new URL(uri);
    return `${url.hostname}${url.pathname}` || uri;
  } catch {
    return uri;
  }
}

function ResearchEvidenceSummary({
  evidence,
}: {
  evidence?: ResearchEvidenceSnapshot;
}) {
  const [expanded, setExpanded] = useState(false);
  const panelId = useId();
  const sources = evidence?.inspectedSources ?? [];
  const citationLabel = evidence ? citationEvidenceLabel(evidence) : null;
  if (sources.length === 0 && !citationLabel) return null;
  const primarySource = sources[0] ? sourceDisplayName(sources[0]) : null;
  const extraCount = Math.max(0, sources.length - 1);
  return (
    <div className="mt-1 max-w-full text-[11px] leading-snug text-secondary/70">
      <button
        type="button"
        className="flex max-w-full flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-black/[0.06] bg-black/[0.025] px-2.5 py-1.5 text-left transition-colors hover:bg-black/[0.04]"
        data-research-evidence-toggle="true"
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="font-medium text-foreground/65">Research evidence</span>
        {sources.length > 0 && (
          <span className="text-secondary/60">
            {sources.length} {sources.length === 1 ? "source" : "sources"}
          </span>
        )}
        {citationLabel && (
          <span className="inline-flex items-center gap-1 text-secondary/65">
            <span
              aria-hidden="true"
              className={`h-1.5 w-1.5 rounded-full ${
                evidence?.citationGate?.verdict === "violation"
                  ? "bg-amber-500"
                  : evidence?.citationGate?.verdict === "ok"
                    ? "bg-emerald-500"
                    : "bg-secondary/30"
              }`}
            />
            {citationLabel}
          </span>
        )}
        {primarySource && (
          <span className="min-w-0 max-w-full truncate text-secondary/55">
            {primarySource}
            {extraCount > 0 ? ` +${extraCount} more` : ""}
          </span>
        )}
      </button>

      {expanded && (
        <div
          id={panelId}
          className="mt-1.5 space-y-1 rounded-lg border border-black/[0.06] bg-white/80 px-2.5 py-2 shadow-sm"
          data-research-evidence-sources="true"
        >
          {sources.map((source) => (
            <div key={source.sourceId} className="min-w-0">
              <div className="truncate font-medium text-foreground/65">
                {sourceDisplayName(source)}
              </div>
              <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-secondary/50">
                <span className="shrink-0 uppercase tracking-wide">
                  {sourceKindDisplayName(source.kind)}
                </span>
                <span className="min-w-0 truncate">{sourceUriDisplayName(source.uri)}</span>
              </div>
            </div>
          ))}
          {sources.length === 0 && citationLabel && (
            <div className="text-secondary/55">{citationLabel}</div>
          )}
        </div>
      )}
    </div>
  );
}

/** Context menu component */
function ContextMenu({ x, y, onAction, onClose }: {
  x: number;
  y: number;
  onAction: (action: MessageContextAction) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const handle = () => onClose();
    document.addEventListener("click", handle);
    document.addEventListener("contextmenu", handle);
    return () => {
      document.removeEventListener("click", handle);
      document.removeEventListener("contextmenu", handle);
    };
  }, [onClose]);

  const menuWidth = 160;
  const menuHeight = 120;
  const clampedX = Math.min(x, window.innerWidth - menuWidth - 8);
  const clampedY = Math.min(y, window.innerHeight - menuHeight - 8);
  const menuStyle: React.CSSProperties = {
    position: "fixed",
    left: Math.max(8, clampedX),
    top: Math.max(8, clampedY),
    zIndex: 50,
  };

  return (
    <div style={menuStyle} className="bg-white rounded-xl shadow-lg border border-black/[0.08] py-1 min-w-[140px] animate-in fade-in zoom-in-95 duration-100">
      <button
        className="w-full px-3 py-2 text-left text-sm text-foreground hover:bg-black/[0.04] transition-colors flex items-center gap-2"
        onMouseDown={(e) => { e.stopPropagation(); onAction("reply"); }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 17 4 12 9 7" />
          <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
        </svg>
        Reply
      </button>
      <button
        className="w-full px-3 py-2 text-left text-sm text-foreground hover:bg-black/[0.04] transition-colors flex items-center gap-2"
        onMouseDown={(e) => { e.stopPropagation(); onAction("copy"); }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
        Copy text
      </button>
      <button
        className="w-full px-3 py-2 text-left text-sm text-foreground hover:bg-black/[0.04] transition-colors flex items-center gap-2"
        onMouseDown={(e) => { e.stopPropagation(); onAction("select"); }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 11 12 14 22 4" />
          <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
        </svg>
        Select
      </button>
    </div>
  );
}

export function MessageBubble({ role, content, timestamp, isStreaming, thinkingContent, thinkingDuration, activities, taskBoard, researchEvidence, usage, botId, replyTo, injected, selectionMode, selected, onSelect, onContextAction }: MessageBubbleProps) {
  const timeStr = useMemo(() => (timestamp ? formatTime(timestamp) : null), [timestamp]);
  const { download, downloadingId } = useAuthDownload();
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const [imagePreview, setImagePreview] = useState<ImagePreview | null>(null);
  const visibleContent = useMemo(
    () => (role === "assistant" ? stripAssistantMetadataPreamble(content) : content),
    [content, role],
  );

  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    if (isStreaming || role === "system" || selectionMode) return;
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY });
  }, [isStreaming, role, selectionMode]);

  const handleContextAction = useCallback((action: MessageContextAction) => {
    setContextMenu(null);
    if (action === "copy") {
      // Selection-aware copy (2026-04-19 fix): prefer user selection over full content.
      const selection = typeof window !== "undefined" ? window.getSelection?.()?.toString() ?? "" : "";
      const textToCopy = buildMessageCopyText({ content: visibleContent, selection });
      navigator.clipboard.writeText(textToCopy).catch(() => {});
    }
    onContextAction?.(action);
  }, [onContextAction, visibleContent]);

  const { refs: kbRefs, text: kbTextContent } = useMemo(() => parseKbContextMarker(visibleContent), [visibleContent]);

  const { textContent, attachments } = useMemo(() => {
    if (!botId) return { textContent: kbTextContent, attachments: [] };
    const markers = parseMarkers(kbTextContent);
    if (markers.length === 0) return { textContent: kbTextContent, attachments: [] };
    let cleaned = kbTextContent;
    for (const m of markers) cleaned = cleaned.replace(m.fullMatch, "").trim();
    const uniqueAttachments = markers.filter((marker, index, allMarkers) =>
      allMarkers.findIndex((candidate) => candidate.id === marker.id) === index
    );
    return { textContent: cleaned, attachments: uniqueAttachments };
  }, [botId, kbTextContent]);

  const isUser = role === "user";
  const hasOpenArchivedTaskBoard =
    !isStreaming &&
    !!taskBoard?.tasks.some((task) => task.status === "pending" || task.status === "in_progress");
  const visibleTaskBoard = hasOpenArchivedTaskBoard ? null : taskBoard;
  const safeTextContent = textContent ?? "";
  const rawContent = isStreaming ? safeTextContent + "\u2588" : safeTextContent;
  // Auto-link pipeline IDs → /dashboard/{botId}/pipelines/{id}
  const displayContent = useMemo(() => {
    if (!rawContent) return "";
    if (!botId || isUser) return rawContent;
    return rawContent.replace(
      /(?<!\]\()\bpipeline-(\d{8}-\d{6})\b(?!\))/g,
      (m) => `[${m}](/dashboard/${botId}/pipelines/${m})`,
    );
  }, [rawContent, botId, isUser]);
  const evidenceSources = researchEvidence?.inspectedSources ?? [];
  const hasMessageBody = displayContent.trim().length > 0 || !!replyTo;
  const messageBodyClassName = isUser
    ? "rounded-2xl px-4 py-2.5 transition-colors overflow-hidden break-words min-w-0 max-w-full bg-black/[0.04] text-foreground rounded-br-md"
    : "w-full min-w-0 max-w-full overflow-hidden break-words py-1 text-foreground";

  // System messages render as a centered divider
  if (role === "system") {
    return (
      <div className="flex items-center gap-3 my-6">
        <div className="flex-1 h-px bg-black/[0.06]" />
        <span className="text-[11px] text-secondary/50 whitespace-nowrap">{content}</span>
        <div className="flex-1 h-px bg-black/[0.06]" />
      </div>
    );
  }

  return (
    <div
      className={`flex ${isUser ? "justify-end" : "justify-start"} mb-4 group ${selectionMode ? "cursor-pointer" : ""}`}
      onContextMenu={handleContextMenu}
      onClick={selectionMode ? onSelect : undefined}
    >
      {/* Checkbox in selection mode */}
      {selectionMode && (
        <div className="flex items-center mr-2 shrink-0">
          <div className={`w-5 h-5 rounded-md border-2 flex items-center justify-center transition-colors ${
            selected ? "bg-[#7C3AED] border-[#7C3AED]" : "border-black/20 bg-white"
          }`}>
            {selected && (
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            )}
          </div>
        </div>
      )}
      <div className={`min-w-0 ${isUser ? "max-w-[88%] sm:max-w-[75%] items-end" : "w-full max-w-full items-start"} flex flex-col gap-1`}>
        {!isUser && ((activities && activities.length > 0) || taskBoard || thinkingContent || (thinkingDuration && thinkingDuration > 0)) && (
          <AgentActivityTimeline
            thinkingContent={thinkingContent}
            thinkingDuration={thinkingDuration}
            activities={activities}
            taskBoard={taskBoard ?? null}
            collapsedByDefault
          />
        )}

        {!isUser && visibleTaskBoard && visibleTaskBoard.tasks.length > 0 && (
          <TaskBoard snapshot={visibleTaskBoard} />
        )}

        {!isUser && researchEvidence && (
          <ResearchEvidenceSummary evidence={researchEvidence} />
        )}

        {hasMessageBody && (
        <div className={messageBodyClassName}>
          {replyTo && (
            <div
              className={`flex items-start gap-1.5 mb-2 -mx-1 px-2 py-1 rounded-md border-l-2 text-xs ${
                isUser
                  ? "bg-black/[0.05] border-black/20 text-foreground/75"
                  : "bg-black/[0.04] border-black/20 text-foreground/75"
              }`}
            >
              <svg
                className="shrink-0 mt-[2px]"
                width="10"
                height="10"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <polyline points="9 17 4 12 9 7" />
                <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
              </svg>
              <span className="min-w-0 truncate leading-snug">
                <span className="font-medium opacity-80">
                  {replyTo.role === "user" ? "You" : "Bot"}
                </span>
                <span className="mx-1 opacity-60">{"\u00b7"}</span>
                <span className="opacity-90">{replyTo.preview}</span>
              </span>
            </div>
          )}
          {isUser ? (
            <p className="text-sm whitespace-pre-wrap leading-relaxed user-msg-text">
              {displayContent}
            </p>
          ) : (
            <div className="prose-chat">
              <ReactMarkdown
                remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
                components={{
                  del: ({ children }) => <>{children}</>,
                  pre: ({ children }) => {
                    const child = Children.only(children);
                    if (isValidElement<{ className?: string; children?: ReactNode }>(child)) {
                      const source = String(child.props.children ?? "").replace(/\n$/, "");
                      const language = /language-(\w+)/.exec(child.props.className ?? "")?.[1]?.toLowerCase();
                      const shouldRenderChart =
                        language === "echarts" ||
                        ((language === "json" || !language) && looksLikeEChartOption(source));

                      if (shouldRenderChart) {
                        return <EChartRenderer source={source} />;
                      }
                    }

                    return <pre>{children}</pre>;
                  },
                  code: ({ className, children, ...props }) => {
                    const language = /language-(\w+)/.exec(className ?? "")?.[1]?.toLowerCase();
                    const shouldRenderChart =
                      language === "echarts" ||
                      language === "json";

                    if (shouldRenderChart) {
                      return <code className={className} {...props}>{children}</code>;
                    }

                    return (
                      <code className={className} {...props}>
                        {children}
                      </code>
                    );
                  },
                  ...(evidenceSources.length > 0 ? {
                    p: ({ children }) => <p>{injectCitationsIntoChildren(children, evidenceSources)}</p>,
                    li: ({ children, ...props }) => <li {...props}>{injectCitationsIntoChildren(children, evidenceSources)}</li>,
                    td: ({ children, ...props }) => <td {...props}>{injectCitationsIntoChildren(children, evidenceSources)}</td>,
                    th: ({ children, ...props }) => <th {...props}>{injectCitationsIntoChildren(children, evidenceSources)}</th>,
                    strong: ({ children }) => <strong>{injectCitationsIntoChildren(children, evidenceSources)}</strong>,
                  } : {}),
                }}
              >
                {displayContent}
              </ReactMarkdown>
            </div>
          )}
        </div>
        )}
        {kbRefs.length > 0 && botId && (
          <div className="flex flex-wrap gap-2 mt-1">
            {kbRefs.map((ref) => {
              const ext = ref.filename.split(".").pop()?.toLowerCase() ?? "";
              const isImage = ["jpg", "jpeg", "png", "gif", "webp"].includes(ext);
              const url = getKnowledgeDocumentUrl(botId, ref.id);

              if (isImage) {
                return (
                  <ExpandableImageAttachment
                    key={ref.id}
                    url={url}
                    filename={ref.filename}
                    onOpen={setImagePreview}
                  />
                );
              }

              return (
                <button
                  key={ref.id}
                  onClick={() => download(url, ref.filename, ref.id)}
                  className="flex items-center gap-2 bg-black/[0.04] border border-black/[0.08] rounded-xl px-3 py-2 hover:bg-black/[0.08] transition-colors cursor-pointer"
                >
                  <svg className="w-5 h-5 text-primary-light shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <path d="M14 2v6h6" />
                  </svg>
                  <span className="text-xs text-foreground truncate max-w-[150px]">{ref.filename}</span>
                </button>
              );
            })}
          </div>
        )}
        {attachments.length > 0 && botId && (
          <div className="flex flex-wrap gap-2 mt-1">
            {attachments.map((att) => {
              const ext = att.filename.split(".").pop()?.toLowerCase() ?? "";
              const isImage = ["jpg", "jpeg", "png", "gif", "webp"].includes(ext);
              const url = getAttachmentUrl(botId, att.id);

              if (isImage) {
                return (
                  <ExpandableImageAttachment
                    key={att.id}
                    url={url}
                    filename={att.filename}
                    onOpen={setImagePreview}
                  />
                );
              }

              const isDownloading = downloadingId === att.id;
              return (
                <button
                  key={att.id}
                  onClick={() => download(url, att.filename, att.id)}
                  disabled={isDownloading}
                  className="flex items-center gap-2 bg-black/[0.04] border border-black/[0.08] rounded-xl px-3 py-2 hover:bg-black/[0.08] transition-colors cursor-pointer disabled:opacity-50"
                >
                  {isDownloading ? (
                    <svg className="w-5 h-5 text-primary-light shrink-0 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
                      <circle cx="12" cy="12" r="10" strokeDasharray="31.4 31.4" strokeLinecap="round" />
                    </svg>
                  ) : (
                    <svg className="w-5 h-5 text-primary-light shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                      <path d="M14 2v6h6" />
                    </svg>
                  )}
                  <span className="text-xs text-foreground truncate max-w-[150px]">{att.filename}</span>
                </button>
              );
            })}
          </div>
        )}
        {timeStr && !isStreaming && (
          <span className={`text-[10px] text-secondary/40 px-1 flex items-center gap-1 ${isUser ? "justify-end" : "justify-start"}`}>
            {injected && isUser && (
              <span
                className="inline-flex items-center gap-0.5 text-[9px] uppercase tracking-wide font-medium text-[#7C3AED]/70"
                title="Delivered mid-turn to the running task"
              >
                <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
                </svg>
                mid-turn
              </span>
            )}
            {!isUser && usage && (
              <>
                <span title="Estimated tokens and model cost for this response">
                  {formatUsageSummary(usage)}
                </span>
                <span aria-hidden="true">·</span>
              </>
            )}
            <span>{timeStr}</span>
          </span>
        )}
      </div>

      {/* Context menu — portal to body to avoid containing block issues from ancestor transforms */}
      {contextMenu && createPortal(
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          onAction={handleContextAction}
          onClose={() => setContextMenu(null)}
        />,
        document.body,
      )}
      <ImageLightbox preview={imagePreview} onClose={() => setImagePreview(null)} />
    </div>
  );
}
