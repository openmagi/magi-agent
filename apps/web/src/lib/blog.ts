import fs from "fs";
import path from "path";
import matter from "gray-matter";
import {
  getBlogPostUrl,
  LOCALE_TO_HREFLANG,
  LOCALIZED_BLOG_LOCALES,
} from "./blog-routes";
import type { BlogLocale } from "./blog-routes";

export type { BlogLocale } from "./blog-routes";
export {
  BLOG_LOCALES,
  getBlogIndexPath,
  getBlogPostPath,
  getBlogPostUrl,
  isBlogLocale,
  LOCALE_TO_BCP47,
  LOCALE_TO_HREFLANG,
  LOCALIZED_BLOG_LOCALES,
} from "./blog-routes";

const CONTENT_DIR = path.join(process.cwd(), "src/content/blog");

export interface BlogPost {
  slug: string;
  title: string;
  description: string;
  date: string;
  tags: string[];
  locale: BlogLocale;
  author: string;
  content: string;
  readingTime: number;
}

export interface BlogPostMeta {
  slug: string;
  title: string;
  description: string;
  date: string;
  tags: string[];
  locale: BlogLocale;
  author: string;
  readingTime: number;
  availableLocales: BlogLocale[];
}

function estimateReadingTime(content: string): number {
  // ~200 wpm for mixed Korean/English content
  const words = content.split(/\s+/).length;
  return Math.max(1, Math.ceil(words / 200));
}

/**
 * Parse filename into slug and locale.
 * "my-post.md" -> { slug: "my-post", locale: "en" }
 * "my-post.ko.md" -> { slug: "my-post", locale: "ko" }
 */
const LOCALE_SUFFIXES = LOCALIZED_BLOG_LOCALES;

export function getBlogAlternateLanguages(
  slug: string,
  siteUrl: string,
): Record<string, string> {
  const availableLocales = getPostLocales(slug);
  const languages: Record<string, string> = {};

  for (const loc of availableLocales) {
    languages[LOCALE_TO_HREFLANG[loc]] = getBlogPostUrl(slug, loc, siteUrl);
  }

  if (availableLocales.includes("en")) {
    languages["x-default"] = getBlogPostUrl(slug, "en", siteUrl);
  }

  return languages;
}

export function getLocalizedBlogStaticParams(): Array<{
  locale: BlogLocale;
  slug: string;
}> {
  return getAllSlugs().flatMap((slug) =>
    getPostLocales(slug)
      .filter((locale) => locale !== "en")
      .map((locale) => ({ locale, slug })),
  );
}

function parseFilename(filename: string): {
  slug: string;
  locale: BlogLocale;
} {
  const base = filename.replace(/\.md$/, "");
  for (const loc of LOCALE_SUFFIXES) {
    if (base.endsWith(`.${loc}`)) {
      return { slug: base.slice(0, -(loc.length + 1)), locale: loc };
    }
  }
  return { slug: base, locale: "en" };
}

function readPost(filePath: string, slug: string): BlogPost | null {
  if (!fs.existsSync(filePath)) return null;
  const raw = fs.readFileSync(filePath, "utf-8");
  const { data, content } = matter(raw);
  return {
    slug,
    title: data.title ?? slug,
    description: data.description ?? "",
    date: data.date ?? "1970-01-01",
    tags: data.tags ?? [],
    locale: data.locale ?? "en",
    author: data.author ?? "openmagi.ai",
    content,
    readingTime: estimateReadingTime(content),
  };
}

/**
 * Get all posts grouped by slug, returning the default (en) version
 * with availableLocales indicating which translations exist.
 */
export function getAllPosts(): BlogPostMeta[] {
  if (!fs.existsSync(CONTENT_DIR)) return [];

  const files = fs.readdirSync(CONTENT_DIR).filter((f) => f.endsWith(".md"));

  // Group by slug
  const slugMap = new Map<string, BlogLocale[]>();
  const postMap = new Map<string, BlogPostMeta>();

  for (const filename of files) {
    const { slug, locale } = parseFilename(filename);
    const locales = slugMap.get(slug) ?? [];
    locales.push(locale);
    slugMap.set(slug, locales);

    // Only store the default (en) version for listing
    if (locale === "en") {
      const raw = fs.readFileSync(
        path.join(CONTENT_DIR, filename),
        "utf-8",
      );
      const { data, content } = matter(raw);
      postMap.set(slug, {
        slug,
        title: data.title ?? slug,
        description: data.description ?? "",
        date: data.date ?? "1970-01-01",
        tags: data.tags ?? [],
        locale: "en",
        author: data.author ?? "openmagi.ai",
        readingTime: estimateReadingTime(content),
        availableLocales: [], // filled below
      });
    }
  }

  // Fill availableLocales and handle posts that only exist in non-en
  for (const [slug, locales] of slugMap) {
    if (postMap.has(slug)) {
      postMap.get(slug)!.availableLocales = locales.sort();
    } else {
      // No en version — use the first available locale
      const locale = locales[0];
      const filename =
        locale === "en" ? `${slug}.md` : `${slug}.${locale}.md`;
      const raw = fs.readFileSync(
        path.join(CONTENT_DIR, filename),
        "utf-8",
      );
      const { data, content } = matter(raw);
      postMap.set(slug, {
        slug,
        title: data.title ?? slug,
        description: data.description ?? "",
        date: data.date ?? "1970-01-01",
        tags: data.tags ?? [],
        locale,
        author: data.author ?? "openmagi.ai",
        readingTime: estimateReadingTime(content),
        availableLocales: locales.sort(),
      });
    }
  }

  return Array.from(postMap.values()).sort(
    (a, b) => new Date(b.date).getTime() - new Date(a.date).getTime(),
  );
}

/**
 * Get a specific post. Tries requested locale first, falls back to "en".
 */
export function getPost(
  slug: string,
  locale: BlogLocale = "en",
): BlogPost | null {
  // Try requested locale
  const localeFile =
    locale === "en"
      ? path.join(CONTENT_DIR, `${slug}.md`)
      : path.join(CONTENT_DIR, `${slug}.${locale}.md`);

  const post = readPost(localeFile, slug);
  if (post) return post;

  // Fall back to en
  if (locale !== "en") {
    return readPost(path.join(CONTENT_DIR, `${slug}.md`), slug);
  }

  return null;
}

/**
 * Get all locale versions of a post.
 */
export function getPostAllLocales(
  slug: string,
): Record<BlogLocale, BlogPost> {
  const result: Partial<Record<BlogLocale, BlogPost>> = {};

  // Check en (default)
  const enPost = readPost(path.join(CONTENT_DIR, `${slug}.md`), slug);
  if (enPost) result.en = enPost;

  // Check other locales
  for (const loc of LOCALE_SUFFIXES) {
    const post = readPost(
      path.join(CONTENT_DIR, `${slug}.${loc}.md`),
      slug,
    );
    if (post) result[loc] = post;
  }

  return result as Record<BlogLocale, BlogPost>;
}

/**
 * Get available locales for a post.
 */
export function getPostLocales(slug: string): BlogLocale[] {
  const locales: BlogLocale[] = [];
  if (fs.existsSync(path.join(CONTENT_DIR, `${slug}.md`))) {
    locales.push("en");
  }
  for (const loc of LOCALE_SUFFIXES) {
    if (fs.existsSync(path.join(CONTENT_DIR, `${slug}.${loc}.md`))) {
      locales.push(loc);
    }
  }
  return locales;
}

export function getAllSlugs(): string[] {
  if (!fs.existsSync(CONTENT_DIR)) return [];
  const files = fs.readdirSync(CONTENT_DIR).filter((f) => f.endsWith(".md"));
  const slugs = new Set<string>();
  for (const f of files) {
    slugs.add(parseFilename(f).slug);
  }
  return Array.from(slugs);
}
