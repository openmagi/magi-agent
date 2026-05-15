import { describe, it, expect, vi, beforeEach } from "vitest";

describe("isAdmin", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("returns true for matching user ID", async () => {
    vi.stubEnv("ADMIN_USER_IDS", "did:privy:abc123,did:privy:def456");
    const { isAdmin } = await import("./guard");
    expect(isAdmin("did:privy:abc123")).toBe(true);
  });

  it("returns false for non-matching user ID", async () => {
    vi.stubEnv("ADMIN_USER_IDS", "did:privy:abc123");
    const { isAdmin } = await import("./guard");
    expect(isAdmin("did:privy:other")).toBe(false);
  });

  it("returns false when env var is empty", async () => {
    vi.stubEnv("ADMIN_USER_IDS", "");
    const { isAdmin } = await import("./guard");
    expect(isAdmin("did:privy:abc123")).toBe(false);
  });

  it("handles whitespace in env var", async () => {
    vi.stubEnv("ADMIN_USER_IDS", " did:privy:abc123 , did:privy:def456 ");
    const { isAdmin } = await import("./guard");
    expect(isAdmin("did:privy:abc123")).toBe(true);
    expect(isAdmin("did:privy:def456")).toBe(true);
  });
});
