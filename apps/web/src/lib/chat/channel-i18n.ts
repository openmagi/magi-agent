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
  "general":      { ko: "일반", ja: "一般", es: "General", zh: "常规" },
  "random":       { ko: "잡담", ja: "雑談", es: "Random", zh: "闲聊" },
  "quick-memo":   { ko: "빠른 메모", ja: "クイックメモ", es: "Nota rápida", zh: "快速备忘" },
  "news":         { ko: "뉴스", ja: "ニュース", es: "Noticias", zh: "新闻" },
  "daily-update": { ko: "데일리 업데이트", ja: "デイリー更新", es: "Actualización diaria", zh: "每日更新" },
  "schedule":     { ko: "일정", ja: "スケジュール", es: "Horario", zh: "日程" },
  "health":       { ko: "건강", ja: "健康", es: "Salud", zh: "健康" },
  "chores":       { ko: "집안일", ja: "家事", es: "Tareas del hogar", zh: "家务" },
  "finance":      { ko: "금융", ja: "金融", es: "Finanzas", zh: "财务" },
  "shopping":     { ko: "쇼핑", ja: "ショッピング", es: "Compras", zh: "购物" },
  "study":        { ko: "학습", ja: "学習", es: "Estudio", zh: "学习" },
  "contacts":     { ko: "연락처", ja: "連絡先", es: "Contactos", zh: "通讯录" },
  "todo-list":    { ko: "할 일", ja: "やることリスト", es: "Lista de tareas", zh: "待办事项" },
  "reminder":     { ko: "리마인더", ja: "リマインダー", es: "Recordatorio", zh: "提醒" },
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
 * Names of the channels shipped by default with every newly-provisioned
 * bot. Used by the sidebar to distinguish user-created (custom) channels
 * from seeded ones — custom channels expose extra affordances like
 * rename + reorder. Keep in sync with provisioning-worker's channel
 * bootstrap list.
 */
export const DEFAULT_CHANNELS: readonly string[] = Object.keys(CHANNEL_LABELS);
