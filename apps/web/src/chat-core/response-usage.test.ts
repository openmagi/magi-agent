import { describe, expect, it } from "vitest";
import {
  normalizeResponseUsage,
  responseUsageFromServerMessage,
  stripResponseUsageMarker,
} from "./response-usage";

describe("response usage metadata", () => {
  it("normalizes server usage metadata", () => {
    expect(
      normalizeResponseUsage({
        inputTokens: 1234.9,
        outputTokens: 56.2,
        costUsd: 0.0123,
      }),
    ).toEqual({
      inputTokens: 1234,
      outputTokens: 56,
      costUsd: 0.0123,
    });
  });

  it("drops incomplete output-only zero-cost usage metadata", () => {
    expect(
      normalizeResponseUsage({
        inputTokens: 0,
        outputTokens: 3048,
        costUsd: 0,
      }),
    ).toBeUndefined();
  });

  it("reads usage from server message rows", () => {
    expect(
      responseUsageFromServerMessage({
        usage: {
          inputTokens: 10,
          outputTokens: 5,
          costUsd: 0.001,
        },
      }),
    ).toEqual({
      inputTokens: 10,
      outputTokens: 5,
      costUsd: 0.001,
    });
  });

  it("strips trailing response usage markers from visible content", () => {
    expect(
      stripResponseUsageMarker(
        "done\n\n<!-- clawy:response-usage:v1:eyJpbnB1dFRva2VucyI6MTB9 -->",
      ),
    ).toBe("done");
  });
});
