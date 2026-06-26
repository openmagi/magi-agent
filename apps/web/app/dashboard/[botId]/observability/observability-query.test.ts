import { describe, expect, it } from "vitest";
import {
  buildActivityQuery,
  CATEGORY_KINDS,
  NOISE_KINDS,
  parseFiltersFromParams,
  filtersToParams,
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
