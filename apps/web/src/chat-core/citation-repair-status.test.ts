import { describe, expect, it } from "vitest";
import {
  citationRepairKindFromStatus,
  deriveCitationRepairStatus,
} from "./citation-repair-status";

describe("citationRepairKindFromStatus", () => {
  it("maps the attribution status to the attribution kind", () => {
    expect(
      citationRepairKindFromStatus("verifying", "citation_attribution"),
    ).toBe("attribution");
  });

  it("maps the induce-search status to the induce_search kind", () => {
    expect(
      citationRepairKindFromStatus("verifying", "citation_induce_search"),
    ).toBe("induce_search");
  });

  it("returns null when the phase is not verifying", () => {
    expect(
      citationRepairKindFromStatus("executing", "citation_attribution"),
    ).toBeNull();
  });

  it("returns null for a non-citation verifying status", () => {
    expect(citationRepairKindFromStatus("verifying", "coding_repair")).toBeNull();
    expect(citationRepairKindFromStatus("verifying", null)).toBeNull();
  });
});

describe("deriveCitationRepairStatus", () => {
  it("is active while streaming with no answer yet during a citation repair", () => {
    expect(
      deriveCitationRepairStatus({
        streaming: true,
        hasAssistantText: false,
        phase: "verifying",
        status: "citation_attribution",
      }),
    ).toBe("attribution");
  });

  it("clears once the repaired answer text has streamed", () => {
    // The affordance must NOT linger over the final answer.
    expect(
      deriveCitationRepairStatus({
        streaming: true,
        hasAssistantText: true,
        phase: "verifying",
        status: "citation_attribution",
      }),
    ).toBeNull();
  });

  it("clears when the turn is no longer streaming", () => {
    expect(
      deriveCitationRepairStatus({
        streaming: false,
        hasAssistantText: false,
        phase: "verifying",
        status: "citation_attribution",
      }),
    ).toBeNull();
  });

  it("is null on a normal turn with no citation status (no ghost UI)", () => {
    expect(
      deriveCitationRepairStatus({
        streaming: true,
        hasAssistantText: false,
        phase: "executing",
        status: null,
      }),
    ).toBeNull();
  });
});
