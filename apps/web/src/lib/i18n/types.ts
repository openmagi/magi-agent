export type Locale = "en" | "ko" | "ja" | "zh" | "es";

export const LOCALES: Locale[] = ["en", "ko", "ja", "zh", "es"];
export const DEFAULT_LOCALE: Locale = "en";
export const LOCALE_LABELS: Record<Locale, string> = {
  en: "English",
  ko: "한국어",
  ja: "日本語",
  zh: "中文",
  es: "Español",
};
