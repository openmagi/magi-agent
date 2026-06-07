"use client";

import type { KbDocReference } from "@/lib/chat/types";

interface KbContextBarProps {
  docs: KbDocReference[];
  onRemove: (docId: string) => void;
}

/** Horizontal tag strip showing selected KB documents above the chat input. */
export function KbContextBar({ docs, onRemove }: KbContextBarProps): React.ReactElement | null {
  if (docs.length === 0) return null;

  return (
    <div className="mb-2 flex items-center gap-1.5 overflow-x-auto scrollbar-hide">
      <span className="shrink-0 text-[10px] font-medium text-secondary/50 uppercase tracking-wide">
        Context
      </span>
      {docs.map((doc) => (
        <span
          key={doc.id}
          className="inline-flex items-center gap-1 shrink-0 max-w-[180px] rounded-lg bg-primary/[0.08] border border-primary/[0.12] px-2 py-1 text-[11px] text-primary-light font-medium"
          title={`${doc.collectionName} / ${doc.filename}`}
        >
          <svg className="w-3 h-3 shrink-0 opacity-60" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <path d="M14 2v6h6" />
          </svg>
          <span className="truncate">{doc.filename}</span>
          <button
            type="button"
            onClick={() => onRemove(doc.id)}
            className="shrink-0 p-0.5 -mr-0.5 rounded text-primary/40 hover:text-primary hover:bg-primary/[0.08] transition-colors cursor-pointer"
            aria-label={`Remove ${doc.filename}`}
          >
            <svg width="10" height="10" viewBox="0 0 20 20" fill="currentColor">
              <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
            </svg>
          </button>
        </span>
      ))}
    </div>
  );
}
