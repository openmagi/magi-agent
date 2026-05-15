import { describe, expect, it, vi } from "vitest";
import { getBlogPostViewTotals } from "./blog-views";

function createBlogViewsClient(
  result: {
    data: Array<{ locale: string; views: string | number | null }> | null;
    error: { message?: string } | null;
  },
) {
  const eq = vi.fn(async () => result);
  const select = vi.fn(() => ({ eq }));
  const from = vi.fn(() => ({ select }));

  return {
    client: { from },
    from,
    select,
    eq,
  };
}

describe("getBlogPostViewTotals", () => {
  it("sums total and per-locale view rows for a blog post", async () => {
    const { client, from, select, eq } = createBlogViewsClient({
      data: [
        { locale: "en", views: "5" },
        { locale: "ko", views: 1 },
        { locale: "en", views: "2" },
      ],
      error: null,
    });

    await expect(
      getBlogPostViewTotals("business-ai-agents-execution-runtime", client),
    ).resolves.toEqual({
      total: 8,
      byLocale: {
        en: 7,
        ko: 1,
      },
    });

    expect(from).toHaveBeenCalledWith("blog_post_views");
    expect(select).toHaveBeenCalledWith("locale, views");
    expect(eq).toHaveBeenCalledWith(
      "slug",
      "business-ai-agents-execution-runtime",
    );
  });

  it("returns zero totals when a post has no view rows", async () => {
    const { client } = createBlogViewsClient({ data: [], error: null });

    await expect(getBlogPostViewTotals("new-post", client)).resolves.toEqual({
      total: 0,
      byLocale: {},
    });
  });

  it("fails open with zero totals when the analytics query fails", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { client } = createBlogViewsClient({
      data: null,
      error: { message: "permission denied" },
    });

    await expect(getBlogPostViewTotals("new-post", client)).resolves.toEqual({
      total: 0,
      byLocale: {},
    });
    expect(warn).toHaveBeenCalledWith(
      "[blog] Failed to load blog view totals",
      expect.objectContaining({ slug: "new-post" }),
    );

    warn.mockRestore();
  });
});
