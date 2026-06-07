export type Locale = "en" | "es" | "zh" | "ja" | "ko";

export const LOCALE_LABELS: Record<Locale, string> = {
  en: "English",
  es: "Español",
  zh: "中文",
  ja: "日本語",
  ko: "한국어",
};

export const LOCALES: Locale[] = ["en", "es", "zh", "ja", "ko"];

export const DEFAULT_LOCALE: Locale = "en";
