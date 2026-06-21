"use client";

/**
 * Compact page-hint card — replaces the long amber/blue prose banners that
 * earlier customize surfaces (Advanced, Gates, Guidance) used to explain
 * themselves. Kevin's UX review on 2026-06-20 flagged those walls as
 * "구구절절 설명" that overwhelmed the actual actions.
 *
 * Shape
 * -----
 *   ┌────────────────────────────────────────────────────────────┐
 *   │ <Title>                                            [tone]   │
 *   │ ✅ what this page CAN do                                    │
 *   │ ✅ what this page CAN do                                    │
 *   │ ❌ what this page can NOT do — pointer to the right surface │
 *   │ ⓘ optional one-line caveat (flag / safety / fail-mode)      │
 *   └────────────────────────────────────────────────────────────┘
 *
 * The card is intentionally a structured list, not prose — every line is
 * a discrete claim the user can scan in under a second.
 */

import React from "react";


export type PageHintTone = "neutral" | "warning";


export interface PageHintItem {
  /** A short imperative or noun phrase (no full sentences). */
  text: React.ReactNode;
}


export interface PageHintProps {
  /** One-line title. Shown bold at the top of the card. */
  title: string;
  /** Things this page CAN do (rendered with a ✅ marker). */
  can?: PageHintItem[];
  /** Things this page can NOT do. Each entry should also point at the
   *  surface that DOES do it (rendered with a ❌ marker). */
  cannot?: PageHintItem[];
  /** Optional single-line caveat (flag, safety, fail-mode). Rendered with
   *  a ⓘ marker. Keep it short — one sentence max. */
  note?: React.ReactNode;
  /** Visual tone. ``warning`` uses an amber accent for pages that mutate
   *  runtime behavior; ``neutral`` is default. */
  tone?: PageHintTone;
}


const TONE_CLS: Record<PageHintTone, string> = {
  neutral: "border-black/[0.08] bg-gray-50/60",
  warning: "border-amber-500/30 bg-amber-50/60",
};


const TITLE_TONE_CLS: Record<PageHintTone, string> = {
  neutral: "text-foreground",
  warning: "text-amber-900",
};


export function PageHint({
  title,
  can = [],
  cannot = [],
  note,
  tone = "neutral",
}: PageHintProps): React.ReactElement {
  return (
    <section
      className={`rounded-xl border px-4 py-3 text-xs leading-relaxed ${TONE_CLS[tone]}`}
      aria-label={`Page hint: ${title}`}
    >
      <p className={`text-sm font-semibold ${TITLE_TONE_CLS[tone]}`}>{title}</p>
      {(can.length > 0 || cannot.length > 0) ? (
        <ul className="mt-2 space-y-1">
          {can.map((item, i) => (
            <li key={`can-${i}`} className="flex items-start gap-2">
              <span aria-hidden="true" className="select-none text-emerald-600">
                ✓
              </span>
              <span className="min-w-0 text-secondary">{item.text}</span>
            </li>
          ))}
          {cannot.map((item, i) => (
            <li key={`cannot-${i}`} className="flex items-start gap-2">
              <span aria-hidden="true" className="select-none text-red-500">
                ✗
              </span>
              <span className="min-w-0 text-secondary">{item.text}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {note ? (
        <p className="mt-2 flex items-start gap-2 text-[11px] text-secondary/80">
          <span aria-hidden="true" className="select-none">ⓘ</span>
          <span className="min-w-0">{note}</span>
        </p>
      ) : null}
    </section>
  );
}
