"use client";

import { useEffect, useState } from "react";
import {
  CITATION_FOCUS_EVENT,
  emitCitationFocus,
  readCitationFocusEvent,
} from "@/chat-core";

interface CitationMarkerChipProps {
  /** Canonical ``src_N`` id this chip resolves to. */
  sourceId: string;
  /** Display index ``n`` rendered as ``[n]``. */
  index: number;
}

/**
 * Inline superscript ``[n]`` citation chip (Wave 3b, Piece A).
 *
 * Carries ``data-source-id`` so the Sources panel can cross-link. Clicking the
 * chip emits a ``CITATION_FOCUS_EVENT`` so the Sources panel scrolls/focuses the
 * matching entry; the chip also listens for the same event and briefly
 * highlights itself when the source is focused from the panel side.
 */
export function CitationMarkerChip({ sourceId, index }: CitationMarkerChipProps) {
  const [highlighted, setHighlighted] = useState(false);

  useEffect(() => {
    const onFocus = (event: Event) => {
      const detail = readCitationFocusEvent(event);
      if (!detail || detail.sourceId !== sourceId) return;
      setHighlighted(true);
      const timer = window.setTimeout(() => setHighlighted(false), 1400);
      return () => window.clearTimeout(timer);
    };
    window.addEventListener(CITATION_FOCUS_EVENT, onFocus);
    return () => window.removeEventListener(CITATION_FOCUS_EVENT, onFocus);
  }, [sourceId]);

  return (
    <sup className="citation-marker-chip-wrap">
      <button
        type="button"
        data-source-id={sourceId}
        data-citation-marker="true"
        aria-label={`Source ${index}`}
        onClick={() => emitCitationFocus(sourceId)}
        className={`ml-0.5 inline-flex items-center rounded px-1 text-[10px] font-semibold leading-tight transition-colors cursor-pointer ${
          highlighted
            ? "bg-[var(--color-accent)] text-white"
            : "bg-[var(--color-accent)]/[0.1] text-[var(--color-accent)] hover:bg-[var(--color-accent)]/[0.2]"
        }`}
      >
        {index}
      </button>
    </sup>
  );
}
