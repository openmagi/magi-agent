"use client";

import { useEffect, useState, type ReactElement } from "react";
import type { BrowserFrame, ChatResponseLanguage } from "@/lib/chat/types";

type BrowserFrameSurface = "work-console" | "run-inspector";

interface BrowserFramePreviewProps {
  frame: BrowserFrame;
  language?: ChatResponseLanguage;
  surface: BrowserFrameSurface;
  className: string;
  imageClassName: string;
}

function isKorean(language?: ChatResponseLanguage): boolean {
  return language === "ko";
}

function t(language: ChatResponseLanguage | undefined, en: string, ko: string): string {
  return isKorean(language) ? ko : en;
}

function browserActionLabel(action: string, language?: ChatResponseLanguage): string {
  switch (action) {
    case "open":
      return t(language, "Opening page", "페이지 여는 중");
    case "click":
    case "mouse_click":
      return t(language, "Clicking", "클릭 중");
    case "fill":
    case "keyboard_type":
    case "press":
      return t(language, "Typing", "입력 중");
    case "scroll":
      return t(language, "Scrolling", "스크롤 중");
    case "screenshot":
    case "snapshot":
      return t(language, "Inspecting page", "페이지 확인 중");
    case "scrape":
      return t(language, "Reading page", "페이지 읽는 중");
    default:
      return t(language, "Using browser", "브라우저 사용 중");
  }
}

function ExpandIcon(): ReactElement {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="m15 3 6 0 0 6" />
      <path strokeLinecap="round" strokeLinejoin="round" d="m9 21-6 0 0-6" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M21 3l-7 7" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 21l7-7" />
    </svg>
  );
}

function CloseIcon(): ReactElement {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

export function BrowserFramePreview({
  frame,
  language,
  surface,
  className,
  imageClassName,
}: BrowserFramePreviewProps): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const imageSrc = `data:${frame.contentType};base64,${frame.imageBase64}`;
  const title = t(language, "Live browser", "실시간 브라우저");
  const expandLabel = t(language, "Open larger browser preview", "브라우저 크게 보기");
  const closeLabel = t(language, "Close larger browser preview", "큰 브라우저 미리보기 닫기");
  const action = browserActionLabel(frame.action, language);
  const detail = frame.url ? `${action} · ${frame.url}` : action;
  const surfaceAttrs =
    surface === "work-console"
      ? { "data-work-console-browser-frame": "true" }
      : { "data-run-inspector-browser-frame": "true" };

  useEffect(() => {
    if (!expanded) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [expanded]);

  return (
    <>
      <section className={className} {...surfaceAttrs}>
        <div className="flex min-w-0 items-center justify-between gap-2 border-b border-black/[0.06] px-2.5 py-1.5">
          <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-secondary/45">
            {title}
          </span>
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="min-w-0 truncate text-[10.5px] text-secondary/55">
              {detail}
            </span>
            <button
              type="button"
              aria-label={expandLabel}
              aria-haspopup="dialog"
              title={expandLabel}
              onClick={() => setExpanded(true)}
              className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-secondary/45 transition-colors hover:bg-black/[0.05] hover:text-secondary/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7C3AED]/40"
              data-browser-frame-expand-trigger="true"
            >
              <ExpandIcon />
            </button>
          </div>
        </div>
        <button
          type="button"
          aria-label={expandLabel}
          aria-haspopup="dialog"
          title={expandLabel}
          onClick={() => setExpanded(true)}
          className="group relative block w-full cursor-zoom-in bg-black/[0.03] text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#7C3AED]/40"
          data-browser-frame-expand-trigger="true"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageSrc}
            alt={t(language, "Browser preview", "브라우저 미리보기")}
            className={imageClassName}
          />
          <span className="pointer-events-none absolute bottom-2 right-2 inline-flex h-7 w-7 items-center justify-center rounded-md border border-white/70 bg-white/85 text-secondary/65 opacity-0 shadow-sm backdrop-blur transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
            <ExpandIcon />
          </span>
        </button>
      </section>

      {expanded && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-3 sm:p-5" data-browser-frame-expanded-viewer="true">
          <button
            type="button"
            aria-label={closeLabel}
            className="absolute inset-0 cursor-zoom-out bg-[#05070D]/75 backdrop-blur-sm"
            onClick={() => setExpanded(false)}
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label={expandLabel}
            className="relative z-10 flex max-h-[94vh] w-[min(96vw,120rem)] flex-col overflow-hidden rounded-xl border border-white/15 bg-white shadow-2xl"
          >
            <div className="flex min-w-0 items-center justify-between gap-3 border-b border-black/[0.08] px-3 py-2">
              <div className="min-w-0">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-secondary/50">
                  {title}
                </div>
                <div className="mt-0.5 truncate text-xs text-secondary/65">{detail}</div>
              </div>
              <button
                type="button"
                aria-label={closeLabel}
                title={closeLabel}
                onClick={() => setExpanded(false)}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-secondary/55 transition-colors hover:bg-black/[0.05] hover:text-secondary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7C3AED]/40"
              >
                <CloseIcon />
              </button>
            </div>
            <div className="min-h-0 flex-1 bg-black/[0.04] p-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={imageSrc}
                alt={t(language, "Large browser preview", "큰 브라우저 미리보기")}
                className="mx-auto block max-h-[calc(94vh-5.5rem)] w-full object-contain"
              />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
