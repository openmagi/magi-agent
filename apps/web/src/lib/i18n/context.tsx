"use client";

import { createContext, useContext, useState, useEffect, useCallback } from "react";
import type { Locale } from "./types";
import { DEFAULT_LOCALE, LOCALES } from "./types";
import en from "./locales/en";
import type { LocaleMessages, Messages } from "./locales/en";

const STORAGE_KEY = "clawy-locale";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function mergeMessageValue<T>(base: T, override: unknown): T {
  if (override === undefined) return base;
  if (Array.isArray(base)) return Array.isArray(override) ? (override as T) : base;
  if (isRecord(base)) {
    if (!isRecord(override)) return base;
    const merged: Record<string, unknown> = { ...base };
    for (const [key, value] of Object.entries(override)) {
      merged[key] = key in base ? mergeMessageValue(base[key as keyof T], value) : value;
    }
    return merged as T;
  }
  return override as T;
}

function mergeMessages(localeMessages: LocaleMessages): Messages {
  return mergeMessageValue(en, localeMessages);
}

async function loadLocaleMessages(locale: Locale): Promise<LocaleMessages> {
  switch (locale) {
    case "es":
      return (await import("./locales/es")).default;
    case "zh":
      return (await import("./locales/zh")).default;
    case "ja":
      return (await import("./locales/ja")).default;
    case "ko":
      return (await import("./locales/ko")).default;
    default:
      return en;
  }
}

async function loadMessages(locale: Locale): Promise<Messages> {
  if (locale === "en") return en;
  return mergeMessages(await loadLocaleMessages(locale));
}

function getInitialLocale(): Locale {
  if (typeof window === "undefined") return DEFAULT_LOCALE;

  // Check localStorage first
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved && LOCALES.includes(saved as Locale)) {
    return saved as Locale;
  }

  // Check browser language
  const lang = navigator.language.slice(0, 2).toLowerCase();
  const map: Record<string, Locale> = { en: "en", es: "es", zh: "zh", ja: "ja", ko: "ko" };
  if (map[lang]) return map[lang];

  return DEFAULT_LOCALE;
}

interface I18nContextValue {
  locale: Locale;
  messages: Messages;
  setLocale: (locale: Locale) => void;
  ready: boolean;
}

const I18nContext = createContext<I18nContextValue | null>(null);

interface I18nProviderProps {
  children: React.ReactNode;
}

export function I18nProvider({ children }: I18nProviderProps) {
  const [locale, setLocaleState] = useState<Locale>(getInitialLocale);
  const [messages, setMessages] = useState<Messages | null>(null);
  const [ready, setReady] = useState(false);

  const setLocale = useCallback((newLocale: Locale) => {
    setLocaleState(newLocale);
    localStorage.setItem(STORAGE_KEY, newLocale);
    document.documentElement.lang = newLocale;
  }, []);

  // Set html lang on mount & run IP-based fallback if no saved/browser locale
  useEffect(() => {
    document.documentElement.lang = locale;

    // Only try IP detection if no saved locale and browser didn't match
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return;

    const browserLang = navigator.language.slice(0, 2).toLowerCase();
    const knownLangs = ["en", "es", "zh", "ja", "ko"];
    if (knownLangs.includes(browserLang)) return;

    const controller = new AbortController();
    fetch("https://ipapi.co/json/", { signal: controller.signal })
      .then((res) => res.json())
      .then((data: { country_code?: string }) => {
        const countryMap: Record<string, Locale> = {
          US: "en", GB: "en", AU: "en", CA: "en", NZ: "en", IE: "en",
          ES: "es", MX: "es", AR: "es", CO: "es", CL: "es", PE: "es", VE: "es",
          CN: "zh", TW: "zh", HK: "zh", SG: "zh",
          JP: "ja",
          KR: "ko",
        };
        const detected = countryMap[data.country_code ?? ""];
        if (detected && detected !== locale) {
          setLocale(detected);
        }
      })
      .catch(() => { /* silently fall back */ });

    return () => controller.abort();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load messages when locale changes
  useEffect(() => {
    let cancelled = false;
    loadMessages(locale).then((msgs) => {
      if (!cancelled) {
        setMessages(msgs);
        setReady(true);
      }
    });
    return () => { cancelled = true; };
  }, [locale]);

  if (!messages) return null;

  return (
    <I18nContext.Provider value={{ locale, messages, setLocale, ready }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return ctx;
}

export function useMessages(): Messages {
  return useI18n().messages;
}
