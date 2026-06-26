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
