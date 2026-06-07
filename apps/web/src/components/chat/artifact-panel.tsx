"use client";

import { useEffect, useRef, useState } from "react";
import { fetchAttachmentBlob, getAttachmentUrl } from "@/lib/chat/attachments";

/** Minimal info needed to open an HTML artifact. */
export interface ArtifactRef {
  /** Attachment UUID on chat-proxy. */
  id: string;
  /** Display filename (used as title fallback). */
  filename: string;
  /** Bot owning the attachment (for auth-scoped fetch). */
  botId: string;
}

/**
 * Inline card shown inside the message bubble for .html artifacts.
 * Clicking "Open" opens the ArtifactPanel (handled by the page-level host).
 */
export function ArtifactCard({ artifact, onOpen }: { artifact: ArtifactRef; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="group flex items-center gap-3 bg-white border border-black/[0.08] rounded-xl px-3 py-2.5 hover:border-[#7C3AED] hover:shadow-sm transition-all text-left cursor-pointer"
    >
      <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-[#7C3AED] to-[#A78BFA] flex items-center justify-center shrink-0">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="16 18 22 12 16 6" />
          <polyline points="8 6 2 12 8 18" />
        </svg>
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-foreground truncate">{artifact.filename}</div>
        <div className="text-[11px] text-secondary/60">Interactive HTML · click to open</div>
      </div>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-secondary/40 group-hover:text-[#7C3AED] transition-colors">
        <path d="M7 17L17 7" />
        <path d="M7 7h10v10" />
      </svg>
    </button>
  );
}

/**
 * Side panel (desktop ≥md) or fullscreen modal (mobile) that renders an HTML
 * artifact in a sandboxed iframe.
 *
 * Security: uses `srcdoc` with `sandbox="allow-scripts"` and no
 * `allow-same-origin` — iframe becomes a null-origin context with no access
 * to parent cookies, localStorage, or DOM.
 */
export function ArtifactPanel({ artifact, onClose }: { artifact: ArtifactRef | null; onClose: () => void }) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!artifact) {
      setHtml(null);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    setHtml(null);
    const ac = new AbortController();
    abortRef.current?.abort();
    abortRef.current = ac;

    (async () => {
      try {
        const url = getAttachmentUrl(artifact.botId, artifact.id);
        const blob = await fetchAttachmentBlob(url);
        if (ac.signal.aborted) return;
        const text = await blob.text();
        if (ac.signal.aborted) return;
        setHtml(text);
      } catch (e) {
        if (ac.signal.aborted) return;
        setError(e instanceof Error ? e.message : "Failed to load artifact");
      } finally {
        if (!ac.signal.aborted) setLoading(false);
      }
    })();

    return () => ac.abort();
  }, [artifact]);

  // Close on Escape
  useEffect(() => {
    if (!artifact) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [artifact, onClose]);

  if (!artifact) return null;

  const downloadUrl = getAttachmentUrl(artifact.botId, artifact.id);

  return (
    <>
      {/* Mobile/desktop overlay. On desktop this is a right-side drawer; on
          mobile it is a fullscreen modal. */}
      <div
        className="fixed inset-0 z-40 bg-black/30 md:bg-black/20 animate-in fade-in duration-150"
        onClick={onClose}
      />
      <aside
        className="fixed z-50 inset-0 pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] md:pt-0 md:pb-0 md:inset-y-0 md:right-0 md:left-auto md:w-[min(680px,55vw)] bg-white md:border-l border-black/[0.08] shadow-2xl flex flex-col animate-in slide-in-from-right duration-200"
        role="dialog"
        aria-label="HTML artifact"
      >
        <header className="flex items-center gap-3 px-4 py-3 border-b border-black/[0.06] shrink-0">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#7C3AED] to-[#A78BFA] flex items-center justify-center shrink-0">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="16 18 22 12 16 6" />
              <polyline points="8 6 2 12 8 18" />
            </svg>
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-foreground truncate">{artifact.filename}</div>
            <div className="text-[11px] text-secondary/60">Sandboxed HTML preview</div>
          </div>
          <a
            href={downloadUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="p-2 rounded-lg hover:bg-black/[0.04] transition-colors text-secondary/70"
            title="Open raw (new tab, auth via browser)"
            aria-label="Open raw"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </a>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-black/[0.04] transition-colors text-secondary/70"
            aria-label="Close"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </header>
        <div className="flex-1 min-h-0 bg-[#FAFAFA] relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="text-xs text-secondary/60">Loading artifact…</div>
            </div>
          )}
          {error && (
            <div className="absolute inset-0 flex items-center justify-center p-4">
              <div className="text-xs text-red-600 bg-red-500/[0.06] rounded-xl px-3 py-2 text-center max-w-md">
                {error}
              </div>
            </div>
          )}
          {html !== null && !error && (
            <iframe
              // null-origin sandbox: no cookie / DOM / storage access to parent.
              // `allow-scripts` enables interactive artifacts; we intentionally
              // do NOT grant `allow-same-origin`, `allow-forms`, `allow-popups`,
              // `allow-top-navigation`.
              sandbox="allow-scripts"
              srcDoc={html}
              className="w-full h-full border-0"
              title={artifact.filename}
            />
          )}
        </div>
      </aside>
    </>
  );
}
