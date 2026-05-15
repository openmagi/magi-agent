export type BlogLocale = "en" | "ko" | "ja" | "zh" | "es";

export const BLOG_LOCALES: BlogLocale[] = ["en", "ko", "ja", "zh", "es"];

export const LOCALIZED_BLOG_LOCALES: BlogLocale[] = ["ko", "ja", "zh", "es"];

export const LOCALE_TO_HREFLANG: Record<BlogLocale, string> = {
  en: "en",
  ko: "ko",
  ja: "ja",
  zh: "zh-Hans",
  es: "es",
};

export const LOCALE_TO_BCP47: Record<BlogLocale, string> = {
  en: "en-US",
  ko: "ko-KR",
  ja: "ja-JP",
  zh: "zh-CN",
  es: "es-ES",
};

export function isBlogLocale(value: string): value is BlogLocale {
  return BLOG_LOCALES.includes(value as BlogLocale);
}

export function getBlogIndexPath(locale: BlogLocale): string {
  return locale === "en" ? "/blog" : `/${locale}/blog`;
}

export function getBlogPostPath(slug: string, locale: BlogLocale): string {
  return locale === "en" ? `/blog/${slug}` : `/${locale}/blog/${slug}`;
}

export function getBlogPostUrl(
  slug: string,
  locale: BlogLocale,
  siteUrl: string,
): string {
  return `${siteUrl.replace(/\/$/, "")}${getBlogPostPath(slug, locale)}`;
}
