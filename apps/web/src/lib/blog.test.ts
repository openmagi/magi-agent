import { describe, expect, it } from "vitest";
import {
  getBlogAlternateLanguages,
  getLocalizedBlogStaticParams,
} from "./blog";
import {
  getBlogIndexPath,
  getBlogPostPath,
  isBlogLocale,
} from "./blog-routes";

describe("blog locale URLs", () => {
  it("keeps English on the canonical root blog path", () => {
    expect(getBlogIndexPath("en")).toBe("/blog");
    expect(getBlogPostPath("context-engineering-for-ai-agents", "en")).toBe(
      "/blog/context-engineering-for-ai-agents",
    );
  });

  it("routes localized posts under a locale prefix", () => {
    expect(getBlogIndexPath("ko")).toBe("/ko/blog");
    expect(getBlogPostPath("context-engineering-for-ai-agents", "ko")).toBe(
      "/ko/blog/context-engineering-for-ai-agents",
    );
  });

  it("builds real hreflang targets for every available post locale", () => {
    expect(
      getBlogAlternateLanguages(
        "context-engineering-for-ai-agents",
        "https://openmagi.ai",
      ),
    ).toEqual({
      en: "https://openmagi.ai/blog/context-engineering-for-ai-agents",
      es: "https://openmagi.ai/es/blog/context-engineering-for-ai-agents",
      ja: "https://openmagi.ai/ja/blog/context-engineering-for-ai-agents",
      ko: "https://openmagi.ai/ko/blog/context-engineering-for-ai-agents",
      "zh-Hans": "https://openmagi.ai/zh/blog/context-engineering-for-ai-agents",
      "x-default": "https://openmagi.ai/blog/context-engineering-for-ai-agents",
    });
  });

  it("generates static params only for non-English localized routes", () => {
    const params = getLocalizedBlogStaticParams();

    expect(params).toContainEqual({
      locale: "ko",
      slug: "context-engineering-for-ai-agents",
    });
    expect(params).not.toContainEqual({
      locale: "en",
      slug: "context-engineering-for-ai-agents",
    });
  });

  it("validates supported blog locales", () => {
    expect(isBlogLocale("ko")).toBe(true);
    expect(isBlogLocale("dashboard")).toBe(false);
  });
});
