import type { ReactNode } from "react";

export interface CitationHedgeCalloutProps {
  /** The hedge body text (sentinel already stripped). */
  children: ReactNode;
}

/**
 * Distinguished callout for the source-citation fail-open hedge (GAP #5).
 *
 * A muted, non-alarming warning tone (an honesty hedge, not an error), matching
 * the existing design-system warning callout token used by the artifact panel
 * (`border-amber-500/20 bg-amber-500/[0.08] text-amber-800`). Accessible via
 * `role="note"` so assistive tech announces it as an aside rather than an alert.
 */
export function CitationHedgeCallout({ children }: CitationHedgeCalloutProps) {
  return (
    <div
      role="note"
      aria-label="Unverified content notice"
      data-testid="citation-hedge-callout"
      className="my-2 flex items-start gap-2 rounded-xl border border-amber-500/20 bg-amber-500/[0.08] px-3 py-2 text-[13px] leading-snug text-amber-800"
    >
      <svg
        width="15"
        height="15"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="mt-0.5 shrink-0 text-amber-600"
        aria-hidden="true"
      >
        <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
        <line x1="12" y1="9" x2="12" y2="13" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
      <span className="min-w-0">{children}</span>
    </div>
  );
}
