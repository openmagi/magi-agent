import { describe, it, expect } from "vitest";
import { classifyTurnMode, classifyTurnModeGated, hasSkipTddSignal } from "./classifier.js";

describe("classifyTurnMode (LLM-based)", () => {
  it("returns other with confidence 1 for empty text", async () => {
    const r = await classifyTurnMode("");
    expect(r).toEqual({ label: "other", confidence: 1 });
  });

  it("returns other with 0.5 confidence when no LLM available", async () => {
    const r = await classifyTurnMode("implement the feature");
    expect(r).toEqual({ label: "other", confidence: 0.5 });
  });

  it("classifyTurnModeGated demotes below floor", async () => {
    const r = await classifyTurnModeGated("something", undefined, 0.6);
    expect(r.label).toBe("other");
  });
});

describe("hasSkipTddSignal (LLM-based)", () => {
  it("returns false when no LLM available", async () => {
    expect(await hasSkipTddSignal("skip tdd")).toBe(false);
  });

  it("returns false for non-test messages", async () => {
    expect(await hasSkipTddSignal("hello world")).toBe(false);
  });
});
