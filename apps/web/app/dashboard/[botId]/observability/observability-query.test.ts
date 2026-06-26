import { describe, expect, it } from "vitest";
import {
  buildActivityQuery,
  buildActivityPageQuery,
  mergeEventsById,
  CATEGORY_KINDS,
  NOISE_KINDS,
  parseFiltersFromParams,
  filtersToParams,
  formatSessionBreakdown,
  extractVerdict,
  resolveKindCategories,
  type ActivityFilters,
} from "./observability-query";

describe("buildActivityQuery", () => {
  it("returns limit-only query when all filters are default (no filters)", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: [],
      sessionId: null,
    };
    const qs = buildActivityQuery(filters);
    expect(qs).toContain("limit=100");
    expect(qs).not.toContain("exclude_kind");
    expect(qs).not.toContain("kind");
    expect(qs).not.toContain("session_id");
  });

  it("adds exclude_kind when hideNoise is ON", () => {
    const filters: ActivityFilters = {
      hideNoise: true,
      selectedKinds: [],
      sessionId: null,
    };
    const qs = buildActivityQuery(filters);
    expect(qs).toContain("exclude_kind=");
    // Must include every noise kind
    for (const kind of NOISE_KINDS) {
      expect(qs).toContain(kind);
    }
  });

  it("does not add exclude_kind when hideNoise is OFF", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: [],
      sessionId: null,
    };
    const qs = buildActivityQuery(filters);
    expect(qs).not.toContain("exclude_kind");
  });

  it("adds kind param when selectedKinds is non-empty", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: ["tool_start", "rule_check"],
      sessionId: null,
    };
    const qs = buildActivityQuery(filters);
    expect(qs).toContain("kind=tool_start%2Crule_check");
  });

  it("adds session_id when session is selected", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: [],
      sessionId: "sess-abc-123",
    };
    const qs = buildActivityQuery(filters);
    expect(qs).toContain("session_id=sess-abc-123");
  });

  it("combines hideNoise + kind + session_id correctly", () => {
    const filters: ActivityFilters = {
      hideNoise: true,
      selectedKinds: ["tool_start"],
      sessionId: "sess-xyz",
    };
    const qs = buildActivityQuery(filters);
    expect(qs).toContain("exclude_kind=");
    expect(qs).toContain("kind=tool_start");
    expect(qs).toContain("session_id=sess-xyz");
    expect(qs).toContain("limit=100");
  });

  it("NOISE_KINDS contains exactly the canonical noise set", () => {
    const required = [
      "text_delta",
      "heartbeat",
      "turn_phase",
      "runtime_trace",
      "tool_progress",
    ];
    for (const k of required) {
      expect(NOISE_KINDS).toContain(k);
    }
    expect(NOISE_KINDS.length).toBe(required.length);
  });

  it("CATEGORY_KINDS contains canonical real runtime kinds (not fictional invented names)", () => {
    const allKinds = Object.values(CATEGORY_KINDS).flat();
    // Real kinds that must be present
    expect(allKinds).toContain("tool_start");
    expect(allKinds).toContain("tool_end");
    expect(allKinds).toContain("rule_check");
    expect(allKinds).toContain("turn_start");
    expect(allKinds).toContain("turn_end");
    expect(allKinds).toContain("aborted");
    expect(allKinds).toContain("artifact_created");
    // Fictional invented kinds that must NOT be present
    expect(allKinds).not.toContain("tool_call");
    expect(allKinds).not.toContain("tool_result");
    expect(allKinds).not.toContain("memory_write");
    expect(allKinds).not.toContain("memory_read");
    expect(allKinds).not.toContain("spawn_agent");
    expect(allKinds).not.toContain("stream_start");
    expect(allKinds).not.toContain("agent_result");
  });

  it("CATEGORY_KINDS does NOT have a 'Noise' group — noise kinds belong in NOISE_KINDS only", () => {
    // The server taxonomy has lifecycle/tools/policy/errors/other + noise_kinds array.
    // Noise kinds are exposed via the Hide-noise toggle (NOISE_KINDS), not the kind
    // multi-select. The FE fallback must match this grouping.
    expect(Object.keys(CATEGORY_KINDS)).not.toContain("Noise");
    // Noise kind values must not appear in CATEGORY_KINDS either
    const allKinds = Object.values(CATEGORY_KINDS).flat();
    for (const noiseKind of NOISE_KINDS) {
      expect(allKinds).not.toContain(noiseKind);
    }
  });

  it("CATEGORY_KINDS groups match the server's canonical category names (case-insensitive)", () => {
    // Server taxonomy groups: lifecycle, tools, policy, errors, other
    const keys = Object.keys(CATEGORY_KINDS).map((k) => k.toLowerCase());
    expect(keys).toContain("lifecycle");
    expect(keys).toContain("tools");
    expect(keys).toContain("policy");
    expect(keys).toContain("errors");
    expect(keys).toContain("other");
  });

  it("produces a valid URL query string (parseable)", () => {
    const filters: ActivityFilters = {
      hideNoise: true,
      selectedKinds: ["tool_start"],
      sessionId: "s1",
    };
    const qs = buildActivityQuery(filters);
    const parsed = new URLSearchParams(qs.replace(/^\?/, ""));
    expect(parsed.get("limit")).toBe("100");
    expect(parsed.get("session_id")).toBe("s1");
    expect(parsed.get("kind")).toBe("tool_start");
    expect(parsed.get("exclude_kind")).toBeTruthy();
  });

  // noiseKinds arg on buildActivityQuery — Finding 1 review fix
  it("uses server noiseKinds for exclude_kind when passed as second arg", () => {
    const serverNoise = ["text_delta", "heartbeat", "server_added_noise"];
    const filters: ActivityFilters = { hideNoise: true, selectedKinds: [], sessionId: null };
    const parsed = new URLSearchParams(
      buildActivityQuery(filters, serverNoise).replace(/^\?/, "")
    );
    expect(parsed.get("exclude_kind")).toBe(serverNoise.join(","));
  });

  it("falls back to NOISE_KINDS when noiseKinds arg is omitted", () => {
    const filters: ActivityFilters = { hideNoise: true, selectedKinds: [], sessionId: null };
    const parsed = new URLSearchParams(
      buildActivityQuery(filters).replace(/^\?/, "")
    );
    expect(parsed.get("exclude_kind")).toBe(NOISE_KINDS.join(","));
  });
});

describe("parseFiltersFromParams", () => {
  it("returns default filters when params are empty", () => {
    const filters = parseFiltersFromParams(new URLSearchParams());
    expect(filters.hideNoise).toBe(true);
    expect(filters.selectedKinds).toEqual([]);
    expect(filters.sessionId).toBeNull();
  });

  it("reads hideNoise=0 as false", () => {
    const filters = parseFiltersFromParams(new URLSearchParams("hideNoise=0"));
    expect(filters.hideNoise).toBe(false);
  });

  it("treats any non-zero hideNoise value as true", () => {
    const filters = parseFiltersFromParams(new URLSearchParams("hideNoise=1"));
    expect(filters.hideNoise).toBe(true);
  });

  it("reads kind param as selectedKinds array", () => {
    const filters = parseFiltersFromParams(
      new URLSearchParams("kind=tool_start%2Crule_check"),
    );
    expect(filters.selectedKinds).toEqual(["tool_start", "rule_check"]);
  });

  it("reads session_id as sessionId", () => {
    const filters = parseFiltersFromParams(
      new URLSearchParams("session_id=sess-abc-123"),
    );
    expect(filters.sessionId).toBe("sess-abc-123");
  });
});

describe("filtersToParams", () => {
  it("omits hideNoise param when true (matches default — minimal URL)", () => {
    const p = filtersToParams({ hideNoise: true, selectedKinds: [], sessionId: null });
    expect(p.get("hideNoise")).toBeNull();
  });

  it("writes hideNoise=0 when false", () => {
    const p = filtersToParams({ hideNoise: false, selectedKinds: [], sessionId: null });
    expect(p.get("hideNoise")).toBe("0");
  });

  it("serializes selectedKinds as comma-joined kind param", () => {
    const p = filtersToParams({
      hideNoise: true,
      selectedKinds: ["tool_start", "rule_check"],
      sessionId: null,
    });
    expect(p.get("kind")).toBe("tool_start,rule_check");
  });

  it("omits kind param when selectedKinds is empty", () => {
    const p = filtersToParams({ hideNoise: true, selectedKinds: [], sessionId: null });
    expect(p.get("kind")).toBeNull();
  });

  it("serializes sessionId as session_id param", () => {
    const p = filtersToParams({
      hideNoise: true,
      selectedKinds: [],
      sessionId: "sess-abc",
    });
    expect(p.get("session_id")).toBe("sess-abc");
  });

  it("omits session_id when sessionId is null", () => {
    const p = filtersToParams({ hideNoise: true, selectedKinds: [], sessionId: null });
    expect(p.get("session_id")).toBeNull();
  });
});

describe("parseFiltersFromParams / filtersToParams round-trip", () => {
  it("round-trips full non-default filter set symmetrically", () => {
    const original: ActivityFilters = {
      hideNoise: false,
      selectedKinds: ["tool_start", "rule_check"],
      sessionId: "sess-xyz",
    };
    const parsed = parseFiltersFromParams(filtersToParams(original));
    expect(parsed).toEqual(original);
  });

  it("round-trips default filters — empty params produce all defaults", () => {
    const original: ActivityFilters = {
      hideNoise: true,
      selectedKinds: [],
      sessionId: null,
    };
    const parsed = parseFiltersFromParams(filtersToParams(original));
    expect(parsed).toEqual(original);
  });

  it("round-trips partial filter (only session set)", () => {
    const original: ActivityFilters = {
      hideNoise: true,
      selectedKinds: [],
      sessionId: "only-session",
    };
    const parsed = parseFiltersFromParams(filtersToParams(original));
    expect(parsed).toEqual(original);
  });
});

describe("buildActivityPageQuery", () => {
  it("with no pagination produces same output as buildActivityQuery", () => {
    const filters: ActivityFilters = {
      hideNoise: true,
      selectedKinds: ["tool_start"],
      sessionId: "sess-abc",
    };
    expect(buildActivityPageQuery(filters)).toBe(buildActivityQuery(filters));
  });

  it("with empty pagination object produces same output as buildActivityQuery", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: ["rule_check"],
      sessionId: null,
    };
    expect(buildActivityPageQuery(filters, {})).toBe(buildActivityQuery(filters));
  });

  it("adds before_id param when beforeId is provided", () => {
    const filters: ActivityFilters = { hideNoise: false, selectedKinds: [], sessionId: null };
    const qs = buildActivityPageQuery(filters, { beforeId: 42 });
    expect(qs).toContain("before_id=42");
    expect(qs).toContain("limit=100");
  });

  it("adds since_id param when sinceId is provided", () => {
    const filters: ActivityFilters = { hideNoise: false, selectedKinds: [], sessionId: null };
    const qs = buildActivityPageQuery(filters, { sinceId: 99 });
    expect(qs).toContain("since_id=99");
    expect(qs).toContain("limit=100");
  });

  it("preserves all active filters alongside before_id", () => {
    const filters: ActivityFilters = {
      hideNoise: true,
      selectedKinds: ["tool_start", "tool_end"],
      sessionId: "sess-xyz",
    };
    const qs = buildActivityPageQuery(filters, { beforeId: 500 });
    const parsed = new URLSearchParams(qs.replace(/^\?/, ""));
    expect(parsed.get("before_id")).toBe("500");
    expect(parsed.get("session_id")).toBe("sess-xyz");
    expect(parsed.get("kind")).toBe("tool_start,tool_end");
    expect(parsed.get("exclude_kind")).toBeTruthy();
    expect(parsed.get("limit")).toBe("100");
    expect(parsed.get("since_id")).toBeNull();
  });

  it("preserves all active filters alongside since_id", () => {
    const filters: ActivityFilters = {
      hideNoise: true,
      selectedKinds: ["rule_check"],
      sessionId: "sess-abc",
    };
    const qs = buildActivityPageQuery(filters, { sinceId: 1000 });
    const parsed = new URLSearchParams(qs.replace(/^\?/, ""));
    expect(parsed.get("since_id")).toBe("1000");
    expect(parsed.get("session_id")).toBe("sess-abc");
    expect(parsed.get("kind")).toBe("rule_check");
    expect(parsed.get("exclude_kind")).toBeTruthy();
    expect(parsed.get("limit")).toBe("100");
    expect(parsed.get("before_id")).toBeNull();
  });

  it("omits both pagination params when neither is provided", () => {
    const filters: ActivityFilters = { hideNoise: false, selectedKinds: [], sessionId: null };
    const qs = buildActivityPageQuery(filters, {});
    expect(qs).not.toContain("before_id");
    expect(qs).not.toContain("since_id");
  });

  it("omits both pagination params when values are null", () => {
    const filters: ActivityFilters = { hideNoise: false, selectedKinds: [], sessionId: null };
    const qs = buildActivityPageQuery(filters, { sinceId: null, beforeId: null });
    expect(qs).not.toContain("before_id");
    expect(qs).not.toContain("since_id");
  });

  it("produces a valid URL query string with pagination (parseable)", () => {
    const filters: ActivityFilters = {
      hideNoise: true,
      selectedKinds: ["tool_start"],
      sessionId: "s1",
    };
    const qs = buildActivityPageQuery(filters, { beforeId: 77 });
    const parsed = new URLSearchParams(qs.replace(/^\?/, ""));
    expect(parsed.get("limit")).toBe("100");
    expect(parsed.get("session_id")).toBe("s1");
    expect(parsed.get("kind")).toBe("tool_start");
    expect(parsed.get("before_id")).toBe("77");
    expect(parsed.get("since_id")).toBeNull();
  });

  // noiseKinds threading — Finding 1 review fix
  it("uses NOISE_KINDS constant for exclude_kind when noiseKinds is absent", () => {
    const filters: ActivityFilters = { hideNoise: true, selectedKinds: [], sessionId: null };
    const parsed = new URLSearchParams(
      buildActivityPageQuery(filters, {}).replace(/^\?/, "")
    );
    expect(parsed.get("exclude_kind")).toBe(NOISE_KINDS.join(","));
  });

  it("uses custom noiseKinds for exclude_kind when provided and non-empty", () => {
    const customNoise = ["text_delta", "heartbeat", "new_noise_kind"];
    const filters: ActivityFilters = { hideNoise: true, selectedKinds: [], sessionId: null };
    const parsed = new URLSearchParams(
      buildActivityPageQuery(filters, { noiseKinds: customNoise }).replace(/^\?/, "")
    );
    expect(parsed.get("exclude_kind")).toBe(customNoise.join(","));
    // Must NOT fall back to constant when custom list is provided
    expect(parsed.get("exclude_kind")).not.toBe(NOISE_KINDS.join(","));
  });

  it("falls back to NOISE_KINDS constant when noiseKinds is an empty array", () => {
    const filters: ActivityFilters = { hideNoise: true, selectedKinds: [], sessionId: null };
    const parsed = new URLSearchParams(
      buildActivityPageQuery(filters, { noiseKinds: [] }).replace(/^\?/, "")
    );
    // Empty noiseKinds = treat as absent → use constant
    expect(parsed.get("exclude_kind")).toBe(NOISE_KINDS.join(","));
  });

  it("noiseKinds does not affect exclude_kind when hideNoise is OFF", () => {
    const customNoise = ["text_delta", "heartbeat"];
    const filters: ActivityFilters = { hideNoise: false, selectedKinds: [], sessionId: null };
    const qs = buildActivityPageQuery(filters, { noiseKinds: customNoise });
    expect(qs).not.toContain("exclude_kind");
  });

  it("custom noiseKinds composes correctly with pagination cursor", () => {
    const customNoise = ["text_delta", "new_runtime_noise"];
    const filters: ActivityFilters = { hideNoise: true, selectedKinds: ["tool_start"], sessionId: "s1" };
    const parsed = new URLSearchParams(
      buildActivityPageQuery(filters, { beforeId: 42, noiseKinds: customNoise }).replace(/^\?/, "")
    );
    expect(parsed.get("exclude_kind")).toBe(customNoise.join(","));
    expect(parsed.get("before_id")).toBe("42");
    expect(parsed.get("kind")).toBe("tool_start");
    expect(parsed.get("session_id")).toBe("s1");
  });
});

describe("formatSessionBreakdown", () => {
  it("returns '0 tools' when all fields are absent (back-compat with older payloads)", () => {
    expect(formatSessionBreakdown({})).toBe("0 tools");
  });

  it("returns '0 tools' when all counts are zero", () => {
    expect(formatSessionBreakdown({ tool_count: 0, rule_check_count: 0, error_count: 0 })).toBe(
      "0 tools",
    );
  });

  it("shows singular 'tool' when tool_count is 1", () => {
    expect(formatSessionBreakdown({ tool_count: 1 })).toBe("1 tool");
  });

  it("shows plural 'tools' when tool_count is > 1", () => {
    expect(formatSessionBreakdown({ tool_count: 3 })).toBe("3 tools");
  });

  it("shows rule count when rule_check_count is non-zero", () => {
    expect(formatSessionBreakdown({ tool_count: 3, rule_check_count: 2 })).toBe(
      "3 tools · 2 rules",
    );
  });

  it("shows singular 'rule' when rule_check_count is 1", () => {
    expect(formatSessionBreakdown({ tool_count: 0, rule_check_count: 1 })).toBe(
      "0 tools · 1 rule",
    );
  });

  it("shows error count when error_count is non-zero", () => {
    expect(formatSessionBreakdown({ tool_count: 3, error_count: 2 })).toBe("3 tools · 2 errors");
  });

  it("shows singular 'error' when error_count is 1", () => {
    expect(formatSessionBreakdown({ tool_count: 5, error_count: 1 })).toBe("5 tools · 1 error");
  });

  it("shows all three parts when all counts are non-zero", () => {
    expect(
      formatSessionBreakdown({ tool_count: 5, rule_check_count: 2, error_count: 3 }),
    ).toBe("5 tools · 2 rules · 3 errors");
  });

  it("hides rules when rule_check_count is zero", () => {
    expect(
      formatSessionBreakdown({ tool_count: 5, rule_check_count: 0, error_count: 2 }),
    ).toBe("5 tools · 2 errors");
  });

  it("hides errors when error_count is zero", () => {
    expect(
      formatSessionBreakdown({ tool_count: 5, rule_check_count: 2, error_count: 0 }),
    ).toBe("5 tools · 2 rules");
  });

  it("treats undefined fields as 0 (back-compat)", () => {
    expect(
      formatSessionBreakdown({ tool_count: undefined, rule_check_count: undefined, error_count: undefined }),
    ).toBe("0 tools");
  });
});

describe("mergeEventsById", () => {
  it("returns existing unchanged when incoming is empty", () => {
    const existing = [{ id: 1, kind: "turn_start" }, { id: 2, kind: "turn_end" }];
    expect(mergeEventsById(existing, [])).toEqual(existing);
  });

  it("returns incoming unchanged when existing is empty", () => {
    const incoming = [{ id: 3 }, { id: 4 }];
    expect(mergeEventsById([], incoming)).toEqual(incoming);
  });

  it("appends incoming events not already in existing", () => {
    const existing = [{ id: 1 }, { id: 2 }];
    const incoming = [{ id: 3 }, { id: 4 }];
    const merged = mergeEventsById(existing, incoming);
    expect(merged.map((e) => e.id)).toEqual([1, 2, 3, 4]);
  });

  it("deduplicates by id — drops incoming events whose id appears in existing", () => {
    const existing = [{ id: 1 }, { id: 2 }, { id: 3 }];
    const incoming = [{ id: 2 }, { id: 3 }, { id: 4 }];
    const merged = mergeEventsById(existing, incoming);
    expect(merged.map((e) => e.id)).toEqual([1, 2, 3, 4]);
  });

  it("simulates load-older — calling mergeEventsById(olderPage, existing) prepends", () => {
    const existing = [{ id: 5 }, { id: 6 }];
    const olderPage = [{ id: 3 }, { id: 4 }];
    const merged = mergeEventsById(olderPage, existing);
    expect(merged.map((e) => e.id)).toEqual([3, 4, 5, 6]);
  });

  it("simulates load-newer — calling mergeEventsById(existing, newerPage) appends", () => {
    const existing = [{ id: 5 }, { id: 6 }];
    const newerPage = [{ id: 7 }, { id: 8 }];
    const merged = mergeEventsById(existing, newerPage);
    expect(merged.map((e) => e.id)).toEqual([5, 6, 7, 8]);
  });

  it("handles events without id — keeps them, never deduplicates on undefined", () => {
    const existing = [{ id: 1 }, { kind: "no-id" }];
    const incoming = [{ id: 2 }, { kind: "also-no-id" }];
    const merged = mergeEventsById(existing, incoming);
    expect(merged.length).toBe(4);
  });

  it("preserves order of both arrays", () => {
    const a = [{ id: 10 }, { id: 20 }];
    const b = [{ id: 30 }, { id: 40 }];
    expect(mergeEventsById(a, b).map((e) => e.id)).toEqual([10, 20, 30, 40]);
  });

  it("does not mutate input arrays", () => {
    const a = [{ id: 1 }];
    const b = [{ id: 2 }];
    const merged = mergeEventsById(a, b);
    expect(a).toEqual([{ id: 1 }]);
    expect(b).toEqual([{ id: 2 }]);
    expect(merged).toEqual([{ id: 1 }, { id: 2 }]);
  });
});

// ---------------------------------------------------------------------------
// Task 9: new filter axes — policyEvidenceOnly, evidenceOnly, errorsOnly
// ---------------------------------------------------------------------------

describe("buildActivityQuery — Task 9 quick-toggle filter axes", () => {
  it("policyEvidenceOnly forces kind=rule_check,rule_violation regardless of selectedKinds", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: ["tool_start"],
      sessionId: null,
      policyEvidenceOnly: true,
    };
    const parsed = new URLSearchParams(buildActivityQuery(filters).replace(/^\?/, ""));
    expect(parsed.get("kind")).toBe("rule_check,rule_violation");
  });

  it("policyEvidenceOnly with empty selectedKinds sets kind=rule_check,rule_violation", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: [],
      sessionId: null,
      policyEvidenceOnly: true,
    };
    const parsed = new URLSearchParams(buildActivityQuery(filters).replace(/^\?/, ""));
    expect(parsed.get("kind")).toBe("rule_check,rule_violation");
  });

  it("evidenceOnly adds has_evidence=true", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: [],
      sessionId: null,
      evidenceOnly: true,
    };
    const parsed = new URLSearchParams(buildActivityQuery(filters).replace(/^\?/, ""));
    expect(parsed.get("has_evidence")).toBe("true");
  });

  it("evidenceOnly with policyEvidenceOnly sets both kind and has_evidence", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: [],
      sessionId: null,
      policyEvidenceOnly: true,
      evidenceOnly: true,
    };
    const parsed = new URLSearchParams(buildActivityQuery(filters).replace(/^\?/, ""));
    expect(parsed.get("kind")).toBe("rule_check,rule_violation");
    expect(parsed.get("has_evidence")).toBe("true");
  });

  it("errorsOnly adds status=error,blocked", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: [],
      sessionId: null,
      errorsOnly: true,
    };
    const parsed = new URLSearchParams(buildActivityQuery(filters).replace(/^\?/, ""));
    expect(parsed.get("status")).toBe("error,blocked");
  });

  it("omits has_evidence when evidenceOnly is absent", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: [],
      sessionId: null,
    };
    const qs = buildActivityQuery(filters);
    expect(qs).not.toContain("has_evidence");
  });

  it("omits status when errorsOnly is absent", () => {
    const filters: ActivityFilters = {
      hideNoise: false,
      selectedKinds: [],
      sessionId: null,
    };
    const qs = buildActivityQuery(filters);
    expect(qs).not.toContain("status");
  });
});

describe("parseFiltersFromParams — Task 9 new axes", () => {
  it("parses policy=1 as policyEvidenceOnly=true", () => {
    const filters = parseFiltersFromParams(new URLSearchParams("policy=1"));
    expect(filters.policyEvidenceOnly).toBe(true);
  });

  it("omits policyEvidenceOnly when policy param is absent", () => {
    const filters = parseFiltersFromParams(new URLSearchParams());
    expect(filters.policyEvidenceOnly).toBeUndefined();
  });

  it("parses evidence=1 as evidenceOnly=true", () => {
    const filters = parseFiltersFromParams(new URLSearchParams("evidence=1"));
    expect(filters.evidenceOnly).toBe(true);
  });

  it("omits evidenceOnly when evidence param is absent", () => {
    const filters = parseFiltersFromParams(new URLSearchParams());
    expect(filters.evidenceOnly).toBeUndefined();
  });

  it("parses errors=1 as errorsOnly=true", () => {
    const filters = parseFiltersFromParams(new URLSearchParams("errors=1"));
    expect(filters.errorsOnly).toBe(true);
  });

  it("omits errorsOnly when errors param is absent", () => {
    const filters = parseFiltersFromParams(new URLSearchParams());
    expect(filters.errorsOnly).toBeUndefined();
  });
});

describe("filtersToParams — Task 9 new axes", () => {
  it("serializes policyEvidenceOnly=true as policy=1", () => {
    const p = filtersToParams({
      hideNoise: true,
      selectedKinds: [],
      sessionId: null,
      policyEvidenceOnly: true,
    });
    expect(p.get("policy")).toBe("1");
  });

  it("omits policy param when policyEvidenceOnly is false/absent", () => {
    const p = filtersToParams({ hideNoise: true, selectedKinds: [], sessionId: null });
    expect(p.get("policy")).toBeNull();
  });

  it("serializes evidenceOnly=true as evidence=1", () => {
    const p = filtersToParams({
      hideNoise: true,
      selectedKinds: [],
      sessionId: null,
      evidenceOnly: true,
    });
    expect(p.get("evidence")).toBe("1");
  });

  it("omits evidence param when evidenceOnly is false/absent", () => {
    const p = filtersToParams({ hideNoise: true, selectedKinds: [], sessionId: null });
    expect(p.get("evidence")).toBeNull();
  });

  it("serializes errorsOnly=true as errors=1", () => {
    const p = filtersToParams({
      hideNoise: true,
      selectedKinds: [],
      sessionId: null,
      errorsOnly: true,
    });
    expect(p.get("errors")).toBe("1");
  });

  it("omits errors param when errorsOnly is false/absent", () => {
    const p = filtersToParams({ hideNoise: true, selectedKinds: [], sessionId: null });
    expect(p.get("errors")).toBeNull();
  });
});

describe("parseFiltersFromParams / filtersToParams round-trip — Task 9 new axes", () => {
  it("round-trips policyEvidenceOnly=true", () => {
    const filters = parseFiltersFromParams(
      filtersToParams({ hideNoise: true, selectedKinds: [], sessionId: null, policyEvidenceOnly: true })
    );
    expect(filters.policyEvidenceOnly).toBe(true);
    expect(filters.evidenceOnly).toBeUndefined();
    expect(filters.errorsOnly).toBeUndefined();
  });

  it("round-trips evidenceOnly=true", () => {
    const filters = parseFiltersFromParams(
      filtersToParams({ hideNoise: true, selectedKinds: [], sessionId: null, evidenceOnly: true })
    );
    expect(filters.evidenceOnly).toBe(true);
    expect(filters.policyEvidenceOnly).toBeUndefined();
  });

  it("round-trips errorsOnly=true", () => {
    const filters = parseFiltersFromParams(
      filtersToParams({ hideNoise: true, selectedKinds: [], sessionId: null, errorsOnly: true })
    );
    expect(filters.errorsOnly).toBe(true);
    expect(filters.policyEvidenceOnly).toBeUndefined();
  });

  it("round-trips all three new axes together", () => {
    const filters = parseFiltersFromParams(
      filtersToParams({
        hideNoise: false,
        selectedKinds: [],
        sessionId: "s1",
        policyEvidenceOnly: true,
        evidenceOnly: true,
        errorsOnly: true,
      })
    );
    expect(filters.hideNoise).toBe(false);
    expect(filters.sessionId).toBe("s1");
    expect(filters.policyEvidenceOnly).toBe(true);
    expect(filters.evidenceOnly).toBe(true);
    expect(filters.errorsOnly).toBe(true);
  });

  it("round-trips without new axes — existing filters unaffected", () => {
    const original: ActivityFilters = {
      hideNoise: false,
      selectedKinds: ["tool_start", "rule_check"],
      sessionId: "sess-xyz",
    };
    const parsed = parseFiltersFromParams(filtersToParams(original));
    expect(parsed.hideNoise).toBe(false);
    expect(parsed.selectedKinds).toEqual(["tool_start", "rule_check"]);
    expect(parsed.sessionId).toBe("sess-xyz");
    expect(parsed.policyEvidenceOnly).toBeUndefined();
    expect(parsed.evidenceOnly).toBeUndefined();
    expect(parsed.errorsOnly).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Task 9: extractVerdict helper
// ---------------------------------------------------------------------------

describe("extractVerdict", () => {
  it("returns null for non-rule_check event kind", () => {
    expect(extractVerdict({ kind: "tool_start", payload: {} })).toBeNull();
  });

  it("returns null for non-rule_check even if payload has verdict", () => {
    expect(extractVerdict({ kind: "turn_start", payload: { verdict: "ok" } })).toBeNull();
  });

  it("returns null for null input", () => {
    expect(extractVerdict(null)).toBeNull();
  });

  it("returns null for undefined input", () => {
    expect(extractVerdict(undefined)).toBeNull();
  });

  it("returns null for non-object input", () => {
    expect(extractVerdict("rule_check")).toBeNull();
    expect(extractVerdict(42)).toBeNull();
  });

  it("extracts verdict from rule_check event with verdict=ok", () => {
    const result = extractVerdict({ kind: "rule_check", payload: { verdict: "ok" } });
    expect(result).not.toBeNull();
    expect(result!.verdict).toBe("ok");
    expect(result!.evidenceRef).toBeNull();
    expect(result!.detail).toBeNull();
  });

  it("extracts verdict from rule_check event with verdict=violation", () => {
    const result = extractVerdict({ kind: "rule_check", payload: { verdict: "violation" } });
    expect(result!.verdict).toBe("violation");
  });

  it("defaults verdict to pending when payload.verdict is absent", () => {
    const result = extractVerdict({ kind: "rule_check", payload: {} });
    expect(result!.verdict).toBe("pending");
  });

  it("defaults verdict to pending when event has no payload", () => {
    const result = extractVerdict({ kind: "rule_check" });
    expect(result!.verdict).toBe("pending");
    expect(result!.evidenceRef).toBeNull();
  });

  it("extracts evidenceRef when present and non-empty", () => {
    const result = extractVerdict({
      kind: "rule_check",
      payload: { verdict: "ok", evidenceRef: "receipt:sha256:abc123def456" },
    });
    expect(result!.evidenceRef).toBe("receipt:sha256:abc123def456");
  });

  it("returns null evidenceRef when evidenceRef is empty string", () => {
    const result = extractVerdict({
      kind: "rule_check",
      payload: { verdict: "ok", evidenceRef: "" },
    });
    expect(result!.evidenceRef).toBeNull();
  });

  it("returns null evidenceRef when evidenceRef is absent from payload", () => {
    const result = extractVerdict({ kind: "rule_check", payload: { verdict: "ok" } });
    expect(result!.evidenceRef).toBeNull();
  });

  it("extracts detail string when present and non-empty", () => {
    const result = extractVerdict({
      kind: "rule_check",
      payload: { verdict: "violation", detail: "rule X violated" },
    });
    expect(result!.detail).toBe("rule X violated");
  });

  it("returns null detail when detail is empty string", () => {
    const result = extractVerdict({
      kind: "rule_check",
      payload: { verdict: "ok", detail: "" },
    });
    expect(result!.detail).toBeNull();
  });

  it("works for rule_violation kind", () => {
    const result = extractVerdict({
      kind: "rule_violation",
      payload: { verdict: "violation", evidenceRef: "sha256:xyz" },
    });
    expect(result).not.toBeNull();
    expect(result!.verdict).toBe("violation");
    expect(result!.evidenceRef).toBe("sha256:xyz");
  });

  it("returns complete RuleCheckVerdict shape with all fields populated", () => {
    const result = extractVerdict({
      kind: "rule_check",
      payload: {
        verdict: "ok",
        evidenceRef: "receipt:sha256:abc123",
        detail: "evidence verdict state=pass: matched=3 missing=0",
      },
    });
    expect(result).toEqual({
      verdict: "ok",
      evidenceRef: "receipt:sha256:abc123",
      detail: "evidence verdict state=pass: matched=3 missing=0",
    });
  });
});

// ---------------------------------------------------------------------------
// Task 9: resolveKindCategories helper
// ---------------------------------------------------------------------------

describe("resolveKindCategories", () => {
  it("returns CATEGORY_KINDS and NOISE_KINDS fallback when metaCategories is null", () => {
    const result = resolveKindCategories(null);
    expect(result.categories).toBe(CATEGORY_KINDS);
    expect(result.noiseKinds).toBe(NOISE_KINDS);
  });

  it("returns fallback when metaCategories is undefined", () => {
    const result = resolveKindCategories(undefined);
    expect(result.categories).toBe(CATEGORY_KINDS);
    expect(result.noiseKinds).toBe(NOISE_KINDS);
  });

  it("returns fallback when metaCategories is a string", () => {
    const result = resolveKindCategories("not-an-object");
    expect(result.categories).toBe(CATEGORY_KINDS);
  });

  it("returns fallback when metaCategories lacks categories key", () => {
    const result = resolveKindCategories({ noise_kinds: ["text_delta"] });
    expect(result.categories).toBe(CATEGORY_KINDS);
  });

  it("returns fallback when categories value is an array (malformed)", () => {
    const result = resolveKindCategories({ categories: ["lifecycle", "tools"] });
    expect(result.categories).toBe(CATEGORY_KINDS);
  });

  it("resolves categories from a well-formed /meta taxonomy payload", () => {
    const metaCats = {
      categories: {
        lifecycle: ["turn_start", "turn_end"],
        tools: ["tool_start", "tool_end"],
        policy: ["rule_check", "rule_violation"],
      },
      noise_kinds: ["text_delta", "heartbeat"],
    };
    const result = resolveKindCategories(metaCats);
    expect(result.categories).toEqual(metaCats.categories);
    expect(result.noiseKinds).toEqual(["text_delta", "heartbeat"]);
  });

  it("falls back to NOISE_KINDS when noise_kinds is absent from payload", () => {
    const metaCats = {
      categories: { lifecycle: ["turn_start"] },
    };
    const result = resolveKindCategories(metaCats);
    expect(result.categories).toEqual({ lifecycle: ["turn_start"] });
    expect(result.noiseKinds).toBe(NOISE_KINDS);
  });

  it("falls back to NOISE_KINDS when noise_kinds is not an array", () => {
    const metaCats = {
      categories: { lifecycle: ["turn_start"] },
      noise_kinds: "text_delta",
    };
    const result = resolveKindCategories(metaCats);
    expect(result.noiseKinds).toBe(NOISE_KINDS);
  });

  it("resolves canonical server taxonomy shape (matching get_meta_taxonomy() output)", () => {
    const serverPayload = {
      categories: {
        lifecycle: ["aborted", "checkpoint", "compaction_end", "compaction_start", "turn_end", "turn_start"],
        tools: ["source_inspected", "tool_end", "tool_start"],
        policy: ["rule_check", "rule_violation"],
        errors: ["aborted", "error"],
        other: ["artifact_created", "child_progress", "task_board"],
      },
      noise_kinds: ["text_delta", "heartbeat", "turn_phase", "runtime_trace", "tool_progress"],
    };
    const result = resolveKindCategories(serverPayload);
    expect(Object.keys(result.categories)).toContain("lifecycle");
    expect(Object.keys(result.categories)).toContain("policy");
    expect(result.categories["policy"]).toContain("rule_check");
    expect(result.noiseKinds).toEqual(serverPayload.noise_kinds);
  });
});
