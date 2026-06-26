import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./page.tsx", import.meta.url),
  "utf8",
);

describe("local OSS observability dashboard", () => {
  it("renders observability from local runtime APIs", () => {
    expect(source).toContain("/api/observability/v1/meta");
    expect(source).toContain("/api/observability/v1/activity");
    expect(source).toContain("/api/observability/v1/sessions");
    expect(source).toContain("/api/observability/v1/health/live");
    expect(source).toContain("/api/observability/v1/board");
    expect(source).toContain("Runtime Observability");
    expect(source).not.toContain("/api/bots/");
    expect(source).not.toContain("Supabase");
    expect(source).not.toContain("Privy");
  });

  it("imports and uses the buildActivityQuery helper from observability-query", () => {
    expect(source).toContain("buildActivityQuery");
    expect(source).toContain("./observability-query");
    // The activity URL must be built via the helper, not a hardcoded query string
    expect(source).toContain("buildActivityQuery(filters)");
  });

  it("defines and uses the NOISE_KINDS constant from observability-query", () => {
    expect(source).toContain("NOISE_KINDS");
  });

  it("has a filter bar with hideNoise toggle (default ON)", () => {
    expect(source).toContain("hideNoise");
    expect(source).toContain("Hide noise");
    expect(source).toContain("DEFAULT_FILTERS");
  });

  it("has kind multi-select from CATEGORY_KINDS", () => {
    expect(source).toContain("CATEGORY_KINDS");
    expect(source).toContain("selectedKinds");
  });

  it("allows selecting a session to scope the feed", () => {
    expect(source).toContain("sessionId");
    expect(source).toContain("handleSessionClick");
  });

  it("marks the Task 9 seam for sourcing categories from /meta", () => {
    expect(source).toContain("TODO(Task 9)");
  });

  it("backs filter state with URL query params via useSearchParams and useRouter", () => {
    expect(source).toContain("useSearchParams");
    expect(source).toContain("useRouter");
    // Writes back to URL via router.replace (not push — avoids polluting history)
    expect(source).toContain("router.replace");
    // Uses the pure serializer pair from observability-query
    expect(source).toContain("parseFiltersFromParams");
    expect(source).toContain("filtersToParams");
  });

  it("wraps the inner page component in a Suspense boundary (required for useSearchParams)", () => {
    expect(source).toContain("Suspense");
    expect(source).toContain("ObservabilityPageInner");
    expect(source).toContain("<Suspense fallback={null}>");
  });

  it("uses a named FilterBarProps interface (code-style: [Component]Props)", () => {
    expect(source).toContain("interface FilterBarProps");
    expect(source).toContain("FilterBarProps");
  });

  it("routes all filter mutations through a single applyFilters function", () => {
    expect(source).toContain("applyFilters");
    // applyFilters must call setFilters and router.replace
    expect(source).toContain("setFilters(next)");
  });

  it("imports and uses buildActivityPageQuery for paginated requests", () => {
    expect(source).toContain("buildActivityPageQuery");
    expect(source).toContain("./observability-query");
  });

  it("imports and uses mergeEventsById for merge/dedupe", () => {
    expect(source).toContain("mergeEventsById");
  });

  it("has a Load older button for backward pagination", () => {
    expect(source).toContain("Load older");
  });

  it("has a Load newer button for forward pagination", () => {
    expect(source).toContain("Load newer");
  });
});
