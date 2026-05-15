import { beforeEach, describe, expect, it, vi } from "vitest";
import { trackBlogPostView } from "./analytics";
import { trackBlogPostViewOnce } from "./blog-view-tracking";

vi.mock("./analytics", () => ({
  trackBlogPostView: vi.fn(),
}));

class MemoryStorage {
  private values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

describe("trackBlogPostViewOnce", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("posts and emits analytics only once per tab for a slug and locale", async () => {
    const storage = new MemoryStorage();
    const fetchImpl = vi.fn(async () => Response.json({ ok: true }));

    await expect(
      trackBlogPostViewOnce("context-engineering-for-ai-agents", "ko", {
        fetchImpl,
        path: "/ko/blog/context-engineering-for-ai-agents",
        storage,
      }),
    ).resolves.toBe(true);

    await expect(
      trackBlogPostViewOnce("context-engineering-for-ai-agents", "ko", {
        fetchImpl,
        path: "/ko/blog/context-engineering-for-ai-agents",
        storage,
      }),
    ).resolves.toBe(false);

    expect(trackBlogPostView).toHaveBeenCalledTimes(1);
    expect(trackBlogPostView).toHaveBeenCalledWith(
      "context-engineering-for-ai-agents",
      "ko",
    );
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledWith("/api/blog/views", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        slug: "context-engineering-for-ai-agents",
        locale: "ko",
        path: "/ko/blog/context-engineering-for-ai-agents",
      }),
      keepalive: true,
    });
  });
});
