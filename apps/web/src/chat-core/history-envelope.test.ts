import { describe, expect, it } from "vitest";
import {
  decodeHistoryPlaintext,
  encodeHistoryPlaintext,
} from "./history-envelope";

describe("chat history plaintext envelopes", () => {
  it("preserves assistant research evidence in the encrypted history envelope", () => {
    const plaintext = encodeHistoryPlaintext({
      role: "assistant",
      content: "final answer",
      thinkingContent: "brief private summary",
      thinkingDuration: 12,
      researchEvidence: {
        inspectedSources: [
          {
            sourceId: "src_1",
            kind: "subagent_result",
            uri: "child-agent://bear-case",
            title: "Bear case partner",
            inspectedAt: 123,
          },
        ],
        citationGate: {
          ruleId: "claim-citation-gate",
          verdict: "ok",
          checkedAt: 456,
        },
        capturedAt: 789,
      },
    });

    expect(plaintext).toContain("\"_v\":3");
    expect(decodeHistoryPlaintext("assistant", plaintext)).toEqual({
      content: "final answer",
      thinkingContent: "brief private summary",
      thinkingDuration: 12,
      researchEvidence: {
        inspectedSources: [
          {
            sourceId: "src_1",
            kind: "subagent_result",
            uri: "child-agent://bear-case",
            title: "Bear case partner",
            inspectedAt: 123,
          },
        ],
        citationGate: {
          ruleId: "claim-citation-gate",
          verdict: "ok",
          checkedAt: 456,
        },
        capturedAt: 789,
      },
    });
  });

  it("preserves assistant usage metadata in the encrypted history envelope", () => {
    const plaintext = encodeHistoryPlaintext({
      role: "assistant",
      content: "final answer",
      usage: {
        inputTokens: 1234,
        outputTokens: 56,
        costUsd: 0.0123,
      },
    });

    expect(plaintext).toContain("\"_v\":3");
    expect(decodeHistoryPlaintext("assistant", plaintext)).toEqual({
      content: "final answer",
      usage: {
        inputTokens: 1234,
        outputTokens: 56,
        costUsd: 0.0123,
      },
    });
  });

  it("drops incomplete output-only zero-cost usage metadata from encrypted history", () => {
    const plaintext = JSON.stringify({
      _v: 3,
      content: "final answer",
      usage: {
        inputTokens: 0,
        outputTokens: 3048,
        costUsd: 0,
      },
    });

    expect(decodeHistoryPlaintext("assistant", plaintext)).toEqual({
      content: "final answer",
    });
  });

  it("continues to decode existing v2 thinking envelopes", () => {
    const plaintext = JSON.stringify({
      _v: 2,
      content: "old answer",
      thinking: "old thinking",
      thinkingDuration: 4,
    });

    expect(decodeHistoryPlaintext("assistant", plaintext)).toEqual({
      content: "old answer",
      thinkingContent: "old thinking",
      thinkingDuration: 4,
    });
  });

  it("does NOT bump the version or persist activities when none are present (byte-identical v2/v3)", () => {
    const withActivities = encodeHistoryPlaintext({
      role: "assistant",
      content: "answer",
      thinkingContent: "t",
      activities: [],
    });
    expect(withActivities).toContain('"_v":2');
    expect(withActivities).not.toContain("activities");
  });

  it("round-trips tool activities through a v4 envelope and restores the timeline rows", () => {
    const plaintext = encodeHistoryPlaintext({
      role: "assistant",
      content: "final answer",
      thinkingContent: "brief",
      thinkingDuration: 35,
      activities: [
        {
          id: "tool-1",
          label: "WebFetch",
          status: "done",
          startedAt: 1700000000000,
          inputPreview: "https://example.com",
          outputPreview: "ok",
          durationMs: 1234,
        },
        {
          id: "tool-2",
          label: "Read",
          // `running` at capture time normalizes to `done` on persist.
          status: "running",
          startedAt: 1700000001000,
        },
      ],
    });

    expect(plaintext).toContain('"_v":4');
    expect(decodeHistoryPlaintext("assistant", plaintext)).toEqual({
      content: "final answer",
      thinkingContent: "brief",
      thinkingDuration: 35,
      activities: [
        {
          id: "tool-1",
          label: "WebFetch",
          status: "done",
          startedAt: 0,
          inputPreview: "https://example.com",
          outputPreview: "ok",
          durationMs: 1234,
        },
        {
          id: "tool-2",
          label: "Read",
          status: "done",
          startedAt: 0,
        },
      ],
    });
  });

  it("preserves error/denied terminal statuses but drops patchPreview and startedAt", () => {
    const plaintext = encodeHistoryPlaintext({
      role: "assistant",
      content: "answer",
      activities: [
        { id: "a", label: "Bash", status: "error", startedAt: 1, outputPreview: "boom" },
        { id: "b", label: "Edit", status: "denied", startedAt: 2 },
      ],
    });

    expect(plaintext).not.toContain("patchPreview");
    expect(plaintext).not.toContain("startedAt");
    expect(decodeHistoryPlaintext("assistant", plaintext).activities).toEqual([
      { id: "a", label: "Bash", status: "error", startedAt: 0, outputPreview: "boom" },
      { id: "b", label: "Edit", status: "denied", startedAt: 0 },
    ]);
  });

  it("caps persisted activities at 50 and preview length at 200", () => {
    const many = Array.from({ length: 60 }, (_, i) => ({
      id: `t-${i}`,
      label: "Read",
      status: "done" as const,
      startedAt: i,
      inputPreview: "x".repeat(500),
    }));
    const decoded = decodeHistoryPlaintext(
      "assistant",
      encodeHistoryPlaintext({ role: "assistant", content: "a", activities: many }),
    );
    expect(decoded.activities).toHaveLength(50);
    expect(decoded.activities?.[0].inputPreview).toHaveLength(200);
  });

  it("ignores an activities field on a v3 envelope (version-gated read)", () => {
    const plaintext = JSON.stringify({
      _v: 3,
      content: "answer",
      activities: [{ id: "x", label: "Read", status: "done" }],
    });
    expect(decodeHistoryPlaintext("assistant", plaintext).activities).toBeUndefined();
  });
});
