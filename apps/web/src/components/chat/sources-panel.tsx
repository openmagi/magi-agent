"use client";

import { useEffect, useRef, useState } from "react";
import {
  CITATION_FOCUS_EVENT,
  emitCitationFocus,
  readCitationFocusEvent,
} from "@/chat-core";
import type { CitationSourceEntry, CitationsPayload } from "@/chat-core";

/** One assistant message's citation payload, for per-message grouping. */
export interface SessionCitationGroup {
  messageId: string;
  timestamp?: number;
  citations: CitationsPayload;
}

interface SourcesPanelProps {
  /** Cited-source payloads for the assistant messages in this session. */
  sessionCitations: SessionCitationGroup[];
}

function sourceHost(uri: string): string {
  if (!uri) return "";
  try {
    return new URL(uri).hostname.replace(/^www\./i, "") || uri;
  } catch {
    return uri;
  }
}

function sourceTitle(entry: CitationSourceEntry): string {
  const title = entry.title?.trim();
  if (title) return title;
  const host = sourceHost(entry.uri);
  return host || entry.sourceId;
}

function kindLabel(kind: CitationSourceEntry["kind"]): string {
  return kind.replace(/_/g, " ");
}

function trustTierClass(tier: CitationSourceEntry["trustTier"]): string {
  switch (tier) {
    case "primary":
      return "bg-emerald-500/15 text-emerald-700";
    case "official":
      return "bg-sky-500/15 text-sky-700";
    case "secondary":
      return "bg-amber-500/15 text-amber-700";
    case "unknown":
    case null:
    default:
      return "bg-black/[0.05] text-secondary/60";
  }
}

/** Globe for web/browser sources, document for everything else. */
function KindIcon({ kind }: { kind: CitationSourceEntry["kind"] }): React.ReactElement {
  const isWeb = kind === "web_search" || kind === "web_fetch" || kind === "browser";
  return isWeb ? (
    <svg className="h-3 w-3 shrink-0 text-secondary/50" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}>
      <circle cx="12" cy="12" r="9" />
      <path strokeLinecap="round" d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18" />
    </svg>
  ) : (
    <svg className="h-3 w-3 shrink-0 text-secondary/50" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M14 2.5H6.5A2 2 0 004.5 4.5v15a2 2 0 002 2h11a2 2 0 002-2V8z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M14 2.5V8h5.5" />
    </svg>
  );
}

function SourceRow({
  entry,
  focused,
  onFocus,
  registerRef,
}: {
  entry: CitationSourceEntry;
  focused: boolean;
  onFocus: (sourceId: string) => void;
  registerRef: (sourceId: string, node: HTMLLIElement | null) => void;
}): React.ReactElement {
  const host = sourceHost(entry.uri);
  const tier = entry.trustTier;
  return (
    <li
      ref={(node) => registerRef(entry.sourceId, node)}
      data-source-id={entry.sourceId}
      data-source-inspected={entry.inspected ? "true" : "false"}
      className={`rounded-lg border px-2.5 py-2 transition-colors ${
        focused
          ? "border-[var(--color-accent)]/40 bg-[var(--color-accent)]/[0.06]"
          : "border-black/[0.06] bg-white/85"
      }`}
    >
      <button
        type="button"
        onClick={() => onFocus(entry.sourceId)}
        className="flex w-full min-w-0 items-start gap-2 text-left cursor-pointer"
        aria-label={`Focus source ${entry.n}`}
      >
        <span className="mt-0.5 inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded bg-[var(--color-accent)]/[0.1] px-1 text-[10px] font-semibold text-[var(--color-accent)]">
          {entry.n}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-1.5">
            <KindIcon kind={entry.kind} />
            <span className="min-w-0 truncate text-[12px] font-medium text-foreground/85" title={sourceTitle(entry)}>
              {sourceTitle(entry)}
            </span>
          </span>
          <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[10px] text-secondary/55">
            <span className="uppercase tracking-wide">{kindLabel(entry.kind)}</span>
            {tier && (
              <span className={`rounded px-1 py-px font-medium uppercase ${trustTierClass(tier)}`}>
                {tier}
              </span>
            )}
            {!entry.inspected && (
              <span
                className="rounded bg-black/[0.04] px-1 py-px text-secondary/50"
                title="Search pointer (not fetched)"
              >
                pointer
              </span>
            )}
            {host && <span className="min-w-0 truncate" title={entry.uri}>{host}</span>}
          </span>
        </span>
      </button>
      {entry.uri && (
        <div className="mt-1 pl-6">
          <a
            href={entry.uri}
            target="_blank"
            rel="noreferrer"
            className="text-[10px] text-[var(--color-accent)] underline-offset-2 hover:underline"
          >
            Open source
          </a>
        </div>
      )}
    </li>
  );
}

/**
 * Sources tab (Wave 3b, Piece B).
 *
 * Lists the session's CITED sources grouped by assistant message from the
 * terminal `citations` payloads. Clicking a row emits a CITATION_FOCUS_EVENT so
 * the matching inline chip highlights; the panel also listens for the same event
 * (fired by a chip click) to scroll/highlight the corresponding entry.
 *
 * NOTE (deferred): the "Consulted, not cited" section (sources registered this
 * session but not cited) is intentionally omitted. The terminal payload is
 * cited-only, and the full session registry is not reachable from the app-route
 * surface: the LocalToolEvidenceCollector that owns the SessionSourceRegistry is
 * built fresh per stream turn (cli/wiring.py) and is not held on the runtime that
 * serves /v1/app/*, nor persisted across turns. Surfacing consulted-not-cited
 * durably needs a backend change (persist the registry snapshot or emit the full
 * set on the terminal frame), which is out of scope for this web-UI-only wave.
 */
export function SourcesPanel({ sessionCitations }: SourcesPanelProps): React.ReactElement {
  const [focusedSourceId, setFocusedSourceId] = useState<string | null>(null);
  const rowRefs = useRef<Map<string, HTMLLIElement>>(new Map());

  const groups = sessionCitations.filter((group) => group.citations.sources.length > 0);

  const registerRef = (sourceId: string, node: HTMLLIElement | null) => {
    if (node) rowRefs.current.set(sourceId, node);
    else rowRefs.current.delete(sourceId);
  };

  useEffect(() => {
    const onFocus = (event: Event) => {
      const detail = readCitationFocusEvent(event);
      if (!detail) return;
      setFocusedSourceId(detail.sourceId);
      const node = rowRefs.current.get(detail.sourceId);
      if (node) node.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
    window.addEventListener(CITATION_FOCUS_EVENT, onFocus);
    return () => window.removeEventListener(CITATION_FOCUS_EVENT, onFocus);
  }, []);

  const handleFocus = (sourceId: string) => {
    setFocusedSourceId(sourceId);
    emitCitationFocus(sourceId);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col" aria-label="Cited sources">
      <div className="border-b border-black/[0.06] px-3 py-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-secondary/70">
          Sources
        </div>
        <p className="mt-1 text-[11px] leading-snug text-secondary/45">
          Sources cited in this conversation. Click a source to find its citation.
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {groups.length === 0 ? (
          <div className="rounded-lg border border-black/[0.06] bg-white/70 px-3 py-3 text-[11.5px] text-secondary/55">
            No sources cited yet.
          </div>
        ) : (
          <div className="space-y-3">
            {groups.map((group, groupIndex) => (
              <section key={group.messageId} data-source-group={group.messageId}>
                <div className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wide text-secondary/45">
                  Response {groups.length > 1 ? groupIndex + 1 : ""}
                </div>
                <ul className="space-y-1.5">
                  {group.citations.sources.map((entry) => (
                    <SourceRow
                      key={`${group.messageId}:${entry.sourceId}`}
                      entry={entry}
                      focused={focusedSourceId === entry.sourceId}
                      onFocus={handleFocus}
                      registerRef={registerRef}
                    />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
