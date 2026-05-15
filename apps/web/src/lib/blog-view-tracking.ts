import { trackBlogPostView } from "./analytics";
import type { BlogLocale } from "./blog-routes";

interface BlogViewStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

interface TrackBlogPostViewOptions {
  fetchImpl?: typeof fetch;
  path?: string;
  storage?: BlogViewStorage | null;
}

function getDefaultStorage(): BlogViewStorage | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage;
}

function getDefaultPath(): string | undefined {
  if (typeof window === "undefined") return undefined;
  return window.location.pathname;
}

function getBlogViewSessionKey(slug: string, locale: BlogLocale): string {
  return `clawy:blog-view:${locale}:${slug}`;
}

export async function trackBlogPostViewOnce(
  slug: string,
  locale: BlogLocale,
  options: TrackBlogPostViewOptions = {},
): Promise<boolean> {
  const storage = options.storage ?? getDefaultStorage();
  const sessionKey = getBlogViewSessionKey(slug, locale);

  if (storage?.getItem(sessionKey)) {
    return false;
  }

  storage?.setItem(sessionKey, "1");
  trackBlogPostView(slug, locale);

  const fetchImpl = options.fetchImpl ?? fetch;
  await fetchImpl("/api/blog/views", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      slug,
      locale,
      path: options.path ?? getDefaultPath(),
    }),
    keepalive: true,
  });

  return true;
}
