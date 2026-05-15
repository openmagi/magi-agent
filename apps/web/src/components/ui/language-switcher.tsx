"use client";

import { useState, useRef, useEffect } from "react";
import { useI18n } from "@/lib/i18n";
import { LOCALES, LOCALE_LABELS } from "@/lib/i18n";
import type { Locale } from "@/lib/i18n";

type LanguageSwitcherMenuPlacement = "bottom" | "top";

interface LanguageSwitcherProps {
  menuPlacement?: LanguageSwitcherMenuPlacement;
  defaultOpen?: boolean;
}

export function getLanguageSwitcherMenuPositionClass(placement: LanguageSwitcherMenuPlacement): string {
  return placement === "top" ? "bottom-full mb-2" : "top-full mt-1";
}

export function LanguageSwitcher({ menuPlacement = "bottom", defaultOpen = false }: LanguageSwitcherProps) {
  const { locale, setLocale } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const ref = useRef<HTMLDivElement>(null);
  const menuPositionClass = getLanguageSwitcherMenuPositionClass(menuPlacement);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  return (
    <div ref={ref} className="relative" data-menu-placement={menuPlacement}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-sm text-secondary hover:text-foreground rounded-lg hover:bg-black/[0.04] transition-colors duration-200 cursor-pointer"
        aria-label="Change language"
        aria-expanded={open}
      >
        <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
        </svg>
        <span>{LOCALE_LABELS[locale]}</span>
        <svg viewBox="0 0 16 16" fill="none" className={`w-3 h-3 transition-transform duration-200 ${open ? "rotate-180" : ""}`} stroke="currentColor" strokeWidth="1.5">
          <path d="M4 6l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className={`absolute right-0 ${menuPositionClass} glass rounded-xl py-1 min-w-[140px] z-50 border border-black/10 shadow-lg max-h-[60dvh] overflow-y-auto`}>
          {LOCALES.map((l: Locale) => (
            <button
              key={l}
              onClick={() => { setLocale(l); setOpen(false); }}
              className={`w-full text-left px-3.5 py-2 text-sm transition-colors duration-150 cursor-pointer ${
                l === locale
                  ? "text-primary-light bg-primary/10"
                  : "text-secondary hover:text-foreground hover:bg-black/[0.04]"
              }`}
            >
              {LOCALE_LABELS[l]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
