import { describe, expect, it } from "vitest";
import { selfClaimVerifierHook } from "./selfClaimVerifier.js";

describe("selfClaimVerifierHook", () => {
  it("is fail-open because the self-claim classifier is advisory LLM work", () => {
    expect(selfClaimVerifierHook.failOpen).toBe(true);
  });
});
