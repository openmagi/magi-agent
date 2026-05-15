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
});
