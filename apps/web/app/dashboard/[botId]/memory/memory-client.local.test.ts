import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  "apps/web/app/dashboard/[botId]/memory/memory-client.tsx",
  "utf8",
);

describe("local OSS memory dashboard", () => {
  it("uses local runtime memory APIs instead of hosted bot APIs", () => {
    expect(source).toContain("@/lib/local-api");
    expect(source).toContain("/v1/app/memory");
    expect(source).toContain("/v1/app/memory/file");
    expect(source).toContain("/v1/app/memory/search");
    expect(source).toContain("/v1/app/memory/files");
    expect(source).not.toContain("/api/bots/");
  });

  it("surfaces memory/archive in its own read-only tier", () => {
    // Dedicated Archive section in the tree.
    expect(source).toContain('label: "Archive"');
    // Archive paths are classified into the archive tier.
    expect(source).toContain('if (path.startsWith("memory/archive/")) return "archive"');
    // Archive is treated as read-only (no edit/delete/bulk-select).
    expect(source).toContain("READ_ONLY_TIERS");
    expect(source).toContain("isReadOnlyPath(selectedFile)");
    expect(source).toContain("isReadOnlyPath(file.path)");
  });
});
