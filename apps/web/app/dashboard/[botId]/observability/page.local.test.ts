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
    // The activity URL must be built via the helper, not a hardcoded query string.
    // Since Finding 1 fix: passes activeNoiseKinds as second arg.
    expect(source).toContain("buildActivityQuery(filters, activeNoiseKinds)");
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

  it("sources kind categories from /meta response when available (Task 9 seam closed)", () => {
    // resolveKindCategories wires /meta taxonomy; CATEGORY_KINDS is the fallback
    expect(source).toContain("resolveKindCategories");
    expect(source).toContain("meta?.categories");
  });

  it("retains CATEGORY_KINDS as fallback for older runtimes", () => {
    expect(source).toContain("CATEGORY_KINDS");
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

  it("imports and uses formatSessionBreakdown from observability-query", () => {
    expect(source).toContain("formatSessionBreakdown");
    // Must be imported from the query helper, not defined inline
    expect(source).toContain("./observability-query");
  });

  it("session card renders session.label prominently with fallback to id", () => {
    // Label is shown; when absent, falls back to session.id
    expect(source).toContain("session.label");
  });

  it("session card uses formatSessionBreakdown for the breakdown line", () => {
    expect(source).toContain("formatSessionBreakdown(session)");
  });

  // --- Task 9 additions ---

  it("has Policy & Evidence only quick toggle (policyEvidenceOnly)", () => {
    expect(source).toContain("policyEvidenceOnly");
    expect(source).toContain("Policy");
  });

  it("has Evidence fired only sub-toggle (evidenceOnly)", () => {
    expect(source).toContain("evidenceOnly");
    expect(source).toContain("Evidence fired only");
  });

  it("has Errors only quick toggle (errorsOnly)", () => {
    expect(source).toContain("errorsOnly");
    expect(source).toContain("Errors only");
  });

  it("renders rule_check verdict using extractVerdict helper", () => {
    expect(source).toContain("extractVerdict");
    expect(source).toContain("verdict");
  });

  it("imports extractVerdict and resolveKindCategories from observability-query", () => {
    expect(source).toContain("extractVerdict");
    expect(source).toContain("resolveKindCategories");
    expect(source).toContain("./observability-query");
  });

  it("adds policyEvidenceOnly and errorsOnly to hasActiveFilters check", () => {
    expect(source).toContain("policyEvidenceOnly");
    // The filter reset reflects the new toggles
    expect(source).toContain("policyEvidenceOnly: false");
  });

  it("includes evidenceOnly in hasActiveFilters so Reset button shows on ?evidence=1", () => {
    // evidenceOnly must participate in the hasActiveFilters expression so that a URL
    // with ?evidence=1 (without ?policy=1) shows the Reset button (Finding 2 fix).
    expect(source).toContain("filters.evidenceOnly");
  });

  it("passes activeNoiseKinds to buildActivityQuery and buildActivityPageQuery (Finding 1 fix)", () => {
    // The noise toggle must use the server noise_kinds when available, not always the
    // hardcoded constant. Page derives activeNoiseKinds from resolveKindCategories and
    // passes it through to the query builders.
    expect(source).toContain("activeNoiseKinds");
    expect(source).toContain("buildActivityQuery(filters, activeNoiseKinds)");
    expect(source).toContain("noiseKinds: activeNoiseKinds");
  });

  // --- Final-review fixes ---

  it("F1: session cards are <button> elements (keyboard-accessible, screen-reader-announced)", () => {
    // Session cards must use <button type='button'> so they are reachable via keyboard
    // (Enter/Space) and announced as interactive by screen readers — not non-semantic <div onClick>.
    expect(source).toContain('<button\n                    type="button"');
    expect(source).toContain("handleSessionClick(session.id)");
    // Must NOT use a bare div with onClick for the session card
    // (div onClick is present elsewhere for non-interactive layout; this just
    //  confirms the session card specifically uses <button>)
    expect(source).toContain("text-left");
    expect(source).toContain("w-full");
  });

  it("F2: applyFilters is wrapped in useCallback (stable identity across renders)", () => {
    // applyFilters must be a useCallback so FilterBar does not re-render every time
    // the parent re-renders (e.g. after pagination state changes).
    expect(source).toContain("const applyFilters = useCallback(");
    // handleSessionClick is also stabilized
    expect(source).toContain("const handleSessionClick = useCallback(");
  });

  it("F3: uses useRef guard to prevent double-fetch when /meta noise_kinds matches FE constant", () => {
    // lastFetchedUrlRef prevents a second full reload when activityUrl doesn't actually
    // change in string value (e.g. /meta returns identical noise_kinds to the FE constant).
    expect(source).toContain("lastFetchedUrlRef");
    expect(source).toContain("useRef<string | null>(null)");
    // Guard must check and update the ref inside loadObservability
    expect(source).toContain("lastFetchedUrlRef.current === activityUrl");
    expect(source).toContain("lastFetchedUrlRef.current = activityUrl");
  });
});
