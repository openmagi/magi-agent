import { createAdminClient } from "@/lib/supabase/admin";
import type { BlogLocale } from "./blog-routes";

export interface BlogViewTotals {
  total: number;
  byLocale: Partial<Record<BlogLocale, number>>;
}

interface BlogViewRow {
  locale: string | null;
  views: string | number | null;
}

interface BlogViewsError {
  message?: string;
}

interface BlogViewsClient {
  from(table: "blog_post_views"): {
    select(columns: "locale, views"): {
      eq(
        column: "slug",
        value: string,
      ): Promise<{ data: BlogViewRow[] | null; error: BlogViewsError | null }>;
    };
  };
}

const EMPTY_TOTALS: BlogViewTotals = {
  total: 0,
  byLocale: {},
};

function normalizeViewCount(value: string | number | null): number {
  const parsed =
    typeof value === "number" ? value : Number.parseInt(String(value ?? "0"), 10);
  if (!Number.isFinite(parsed) || parsed < 0) return 0;
  return Math.floor(parsed);
}

function isBlogLocaleValue(value: string | null): value is BlogLocale {
  return (
    value === "en" ||
    value === "ko" ||
    value === "ja" ||
    value === "zh" ||
    value === "es"
  );
}

export async function getBlogPostViewTotals(
  slug: string,
  client?: BlogViewsClient,
): Promise<BlogViewTotals> {
  try {
    const supabase =
      client ?? (createAdminClient() as unknown as BlogViewsClient);
    const { data, error } = await supabase
      .from("blog_post_views")
      .select("locale, views")
      .eq("slug", slug);

    if (error) {
      console.warn("[blog] Failed to load blog view totals", {
        slug,
        message: error.message,
      });
      return EMPTY_TOTALS;
    }

    const totals: BlogViewTotals = { total: 0, byLocale: {} };
    for (const row of data ?? []) {
      const views = normalizeViewCount(row.views);
      totals.total += views;
      if (isBlogLocaleValue(row.locale)) {
        totals.byLocale[row.locale] = (totals.byLocale[row.locale] ?? 0) + views;
      }
    }

    return totals;
  } catch (error) {
    console.warn("[blog] Failed to load blog view totals", {
      slug,
      message: error instanceof Error ? error.message : String(error),
    });
    return EMPTY_TOTALS;
  }
}
