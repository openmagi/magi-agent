import { describe, expect, it } from "vitest";
import {
  buildActivityQuery,
  CATEGORY_KINDS,
  NOISE_KINDS,
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
      selectedKinds: ["tool_call", "tool_result"],
      sessionId: null,
    };
    const qs = buildActivityQuery(filters);
    expect(qs).toContain("kind=tool_call%2Ctool_result");
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
      selectedKinds: ["tool_call"],
      sessionId: "sess-xyz",
    };
    const qs = buildActivityQuery(filters);
    expect(qs).toContain("exclude_kind=");
    expect(qs).toContain("kind=tool_call");
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
      selectedKinds: ["tool_call"],
      sessionId: "s1",
    };
    const qs = buildActivityQuery(filters);
    const parsed = new URLSearchParams(qs.replace(/^\?/, ""));
    expect(parsed.get("limit")).toBe("100");
    expect(parsed.get("session_id")).toBe("s1");
    expect(parsed.get("kind")).toBe("tool_call");
    expect(parsed.get("exclude_kind")).toBeTruthy();
  });
});
