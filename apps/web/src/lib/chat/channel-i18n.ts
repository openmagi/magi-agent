import type { Locale } from "@/lib/i18n/types";

const CATEGORY_LABELS: Record<string, Partial<Record<Locale, string>>> = {
  General: { ko: "일반", ja: "一般", es: "General", zh: "常规" },
  Info:    { ko: "정보", ja: "情報", es: "Info", zh: "资讯" },
  Life:    { ko: "생활", ja: "生活", es: "Vida", zh: "生活" },
  Finance: { ko: "재정", ja: "財務", es: "Finanzas", zh: "财务" },
  Study:   { ko: "학습", ja: "学習", es: "Estudio", zh: "学习" },
  People:  { ko: "사람", ja: "人物", es: "Personas", zh: "人物" },
  Tasks:   { ko: "할일", ja: "タスク", es: "Tareas", zh: "任务" },
  Other:   { ko: "기타", ja: "その他", es: "Otros", zh: "其他" },
};

const CHANNEL_LABELS: Record<string, Partial<Record<Locale, string>>> = {
  "general": { ko: "일반", ja: "一般", es: "General", zh: "常规" },
};

/** Localized category label. Falls back to the English key if no translation. */
export function localizeCategory(category: string, locale: Locale): string {
  if (locale === "en") return category;
  return CATEGORY_LABELS[category]?.[locale] ?? category;
}

/** Localized channel display name. Only translates known default channels. */
export function localizeChannel(name: string, displayName: string | null, locale: Locale): string {
  if (locale === "en") return displayName || name;
  return CHANNEL_LABELS[name]?.[locale] ?? displayName ?? name;
}

/**
 * Names of platform-provided channels. Other channels, including legacy
 * seeded rows on existing bots, are treated like user-managed channels.
 */
export const DEFAULT_CHANNELS: readonly string[] = Object.keys(CHANNEL_LABELS);
