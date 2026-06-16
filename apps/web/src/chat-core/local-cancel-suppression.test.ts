import { describe, expect, it } from "vitest";
import {
  clearLocalCancelSuppression,
  isLocalCancelSuppressed,
  markLocalCancelSuppressed,
} from "./local-cancel-suppression";

describe("local cancel suppression", () => {
  it("temporarily suppresses stale live updates after a local stop", () => {
    const suppressions: Record<string, number> = {};

    markLocalCancelSuppressed(suppressions, "general", 1_000, 5_000);

    expect(isLocalCancelSuppressed(suppressions, "general", 5_999)).toBe(true);
    expect(isLocalCancelSuppressed(suppressions, "general", 6_000)).toBe(false);
    expect(suppressions.general).toBeUndefined();
  });

  it("clears a local stop suppression when a fresh user turn starts", () => {
    const suppressions: Record<string, number> = {};

    markLocalCancelSuppressed(suppressions, "general", 1_000, 5_000);
    clearLocalCancelSuppression(suppressions, "general");

    expect(isLocalCancelSuppressed(suppressions, "general", 1_001)).toBe(false);
  });
});
