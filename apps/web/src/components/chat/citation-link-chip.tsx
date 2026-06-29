"use client";

import { useCallback, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { matchCitationSource } from "../../chat-core/citation-source-match";
import type { InspectedSource } from "../../chat-core/types";

interface CitationLinkChipProps {
  /** href the model emitted via standard markdown link syntax. */
  href?: string;
  /** Original anchor label so unmatched links render as before. */
  children?: ReactNode;
  /** Snapshot of inspected sources for the current message / turn. */
  sources: readonly InspectedSource[];
}

function sourceKindDisplayName(kind: InspectedSource["kind"]): string {
  return kind.replace(/_/g, " ");
}

function sourceHostDisplayName(uri: string): string {
  try {
    const url = new URL(uri);
    return url.hostname.replace(/^www\./i, "");
  } catch {
    return uri;
  }
}

function trustTierBadgeClass(tier: InspectedSource["trustTier"]): string {
  switch (tier) {
    case "primary":
      return "bg-emerald-500/20 text-emerald-800";
    case "official":
      return "bg-sky-500/20 text-sky-800";
    case "secondary":
      return "bg-amber-500/20 text-amber-800";
    case "unknown":
    default:
      return "bg-black/[0.06] text-secondary/65";
  }
}

/**
 * Anchor renderer for ReactMarkdown.  When the link's href resolves to an
 * entry in ``sources`` (via the strict ``matchCitationSource`` matcher), the
 * link renders as a citation chip with a hover popover showing the source's
 * title / kind / host / trust tier / first snippet.  When the href does not
 * match any inspected source, the chip falls back to a plain ``<a>`` so
 * external links keep working untouched.
 *
 * This sits ALONGSIDE the legacy ``[src_<id>]`` literal-marker chip
 * (``CitationBadge``); the two systems are independent.  Markdown-link
 * citations come from the new ``CITATION_CONVENTION_BLOCK`` (#1127) system
 * prompt.
 */
export function CitationLinkChip({
  href,
  children,
  sources,
}: CitationLinkChipProps) {
  const matched = href ? matchCitationSource(href, sources) : null;

  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const chipRef = useCallback(
    (node: HTMLElement | null) => {
      if (!node || !open) return;
      const rect = node.getBoundingClientRect();
      setPos({ top: rect.bottom + 4, left: rect.left + rect.width / 2 });
    },
    [open],
  );

  if (!matched || !href) {
    return (
      <a
        href={href ?? "#"}
        target="_blank"
        rel="noreferrer"
        className="text-[var(--color-accent)] underline-offset-2 hover:underline"
      >
        {children}
      </a>
    );
  }

  const host = sourceHostDisplayName(matched.uri);
  const title = matched.title?.trim() || host;
  const tier = matched.trustTier ?? "unknown";

  return (
    <>
      <a
        ref={chipRef}
        href={href}
        target="_blank"
        rel="noreferrer"
        data-citation-link-chip="matched"
        data-citation-source-id={matched.sourceId}
        className="citation-link-chip inline-flex items-baseline gap-0.5 rounded-md bg-[var(--color-accent)]/[0.08] px-1 py-0 text-[var(--color-accent)] underline-offset-2 hover:bg-[var(--color-accent)]/[0.14] hover:underline"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        {children}
        <span
          className="ml-0.5 inline-block text-[10px] text-secondary/55"
          aria-hidden="true"
        >
          ⌗
        </span>
      </a>
      {open && pos && typeof document !== "undefined" &&
        createPortal(
          <div
            className="citation-link-tooltip fixed z-50 -translate-x-1/2 rounded-lg border border-black/10 bg-white px-2.5 py-1.5 text-[11px] shadow-md"
            style={{ top: pos.top, left: pos.left }}
            data-citation-link-tooltip="true"
          >
            <div className="max-w-[280px] truncate font-medium text-foreground/80">
              {title}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-secondary/55">
              <span className="uppercase tracking-wide">
                {sourceKindDisplayName(matched.kind)}
              </span>
              <span
                className={`rounded px-1 py-px text-[9px] font-medium uppercase ${trustTierBadgeClass(tier)}`}
              >
                {tier}
              </span>
              <span className="truncate max-w-[180px]">{host}</span>
            </div>
            {matched.snippets && matched.snippets.length > 0 && (
              <div className="mt-1 max-w-[280px] border-t border-black/[0.06] pt-1 text-[10px] leading-snug text-secondary/65 line-clamp-2">
                {matched.snippets[0]}
              </div>
            )}
          </div>,
          document.body,
        )}
    </>
  );
}
