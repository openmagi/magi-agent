import { describe, it, expect } from "vitest";
import { ClassifierExtensionRegistry } from "./classifierExtensions.js";
import type { ClassifierDimensionDef } from "./classifierExtensions.js";

describe("ClassifierExtensionRegistry", () => {
  function makeDim(
    overrides?: Partial<ClassifierDimensionDef>,
  ): ClassifierDimensionDef {
    return {
      name: overrides?.name ?? "test_dim",
      phase: overrides?.phase ?? "request",
      schema: overrides?.schema ?? { score: "number" },
      instructions: overrides?.instructions ?? "Rate the request 1-5",
    };
  }

  it("should register and list dimensions", () => {
    const reg = new ClassifierExtensionRegistry();
    reg.register(makeDim({ name: "dim_a" }));
    reg.register(makeDim({ name: "dim_b", phase: "finalAnswer" }));

    expect(reg.size).toBe(2);
    expect(reg.list()).toHaveLength(2);
  });

  it("should filter by phase", () => {
    const reg = new ClassifierExtensionRegistry();
    reg.register(makeDim({ name: "req1", phase: "request" }));
    reg.register(makeDim({ name: "req2", phase: "request" }));
    reg.register(makeDim({ name: "final1", phase: "finalAnswer" }));

    expect(reg.getByPhase("request")).toHaveLength(2);
    expect(reg.getByPhase("finalAnswer")).toHaveLength(1);
  });

  it("should enforce max custom dimensions", () => {
    const reg = new ClassifierExtensionRegistry();
    for (let i = 0; i < 10; i++) {
      reg.register(makeDim({ name: `dim_${i}` }));
    }
    expect(() => reg.register(makeDim({ name: "dim_11" }))).toThrow(
      "max 10 custom classifier dimensions allowed",
    );
  });

  it("should unregister a dimension", () => {
    const reg = new ClassifierExtensionRegistry();
    reg.register(makeDim({ name: "removeme" }));
    expect(reg.size).toBe(1);
    expect(reg.unregister("removeme")).toBe(true);
    expect(reg.size).toBe(0);
    expect(reg.unregister("nonexistent")).toBe(false);
  });

  it("should build extended system prompt with custom dimensions", () => {
    const reg = new ClassifierExtensionRegistry();
    reg.register(
      makeDim({
        name: "urgency",
        phase: "request",
        schema: { level: "string", score: "number" },
        instructions: "Classify urgency of the request.",
      }),
    );

    const base = "You are a classifier.";
    const extended = reg.buildExtendedSystemPrompt(base, "request");
    expect(extended).toContain("You are a classifier.");
    expect(extended).toContain("Custom Classifier Dimensions");
    expect(extended).toContain("urgency");
    expect(extended).toContain("level: string");
    expect(extended).toContain("score: number");
    expect(extended).toContain("Classify urgency of the request.");
  });

  it("should return base prompt when no dimensions for phase", () => {
    const reg = new ClassifierExtensionRegistry();
    reg.register(makeDim({ name: "req_only", phase: "request" }));

    const base = "Base prompt.";
    expect(reg.buildExtendedSystemPrompt(base, "finalAnswer")).toBe(base);
  });

  it("should parse extended output and extract custom dimension data", () => {
    const reg = new ClassifierExtensionRegistry();
    reg.register(makeDim({ name: "urgency", phase: "request" }));
    reg.register(makeDim({ name: "sentiment", phase: "request" }));

    const raw = {
      urgency: { level: "high", score: 5 },
      sentiment: { polarity: "positive" },
      builtinField: true,
    };

    const result = reg.parseExtendedOutput(raw, "request");
    expect(result).toBeDefined();
    expect(result!.urgency).toEqual({ level: "high", score: 5 });
    expect(result!.sentiment).toEqual({ polarity: "positive" });
    expect(result!.builtinField).toBeUndefined();
  });

  it("should return undefined when no custom dimensions present in output", () => {
    const reg = new ClassifierExtensionRegistry();
    reg.register(makeDim({ name: "urgency", phase: "request" }));

    const raw = { builtinField: true };
    const result = reg.parseExtendedOutput(raw, "request");
    expect(result).toBeUndefined();
  });

  it("should estimate extra tokens", () => {
    const reg = new ClassifierExtensionRegistry();
    reg.register(
      makeDim({
        name: "test",
        instructions: "a".repeat(100),
        schema: { a: "string", b: "number" },
      }),
    );

    const tokens = reg.estimateExtraTokens();
    expect(tokens).toBeGreaterThan(0);
    expect(tokens).toBe(Math.ceil(100 / 4) + 2 * 10);
  });
});
