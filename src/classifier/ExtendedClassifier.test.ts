import { describe, it, expect } from "vitest";

import {
  buildCustomDimensionPrompt,
  hasCustomDimensions,
  parseClassifierResponse,
  extractCustomClassification,
} from "./ExtendedClassifier.js";
import type { ClassifierConfig } from "../config/MagiConfig.js";

function makeConfig(
  dims: ClassifierConfig["custom_dimensions"] = {},
): ClassifierConfig {
  return { custom_dimensions: dims };
}

describe("ExtendedClassifier", () => {
  describe("buildCustomDimensionPrompt", () => {
    it("returns empty string when no dimensions configured", () => {
      const result = buildCustomDimensionPrompt(makeConfig(), "request");
      expect(result).toBe("");
    });

    it("returns empty string when no dimensions match phase", () => {
      const config = makeConfig({
        safety: {
          phase: "final_answer",
          prompt: "Is this safe?",
          output_schema: { is_safe: "boolean" },
        },
      });
      const result = buildCustomDimensionPrompt(config, "request");
      expect(result).toBe("");
    });

    it("builds prompt for matching phase dimensions", () => {
      const config = makeConfig({
        tone: {
          phase: "request",
          prompt: "Classify the tone of this request.",
          output_schema: { tone: "string", formality: "number" },
        },
      });
      const result = buildCustomDimensionPrompt(config, "request");
      expect(result).toContain("Custom Dimension: tone");
      expect(result).toContain("Classify the tone of this request.");
      expect(result).toContain('"tone": string');
      expect(result).toContain('"formality": number');
    });

    it("builds multiple dimension prompts", () => {
      const config = makeConfig({
        safety: {
          phase: "final_answer",
          prompt: "Safety check.",
          output_schema: { safe: "boolean" },
        },
        quality: {
          phase: "final_answer",
          prompt: "Quality check.",
          output_schema: { score: "number" },
        },
      });
      const result = buildCustomDimensionPrompt(config, "final_answer");
      expect(result).toContain("Custom Dimension: safety");
      expect(result).toContain("Custom Dimension: quality");
    });
  });

  describe("hasCustomDimensions", () => {
    it("returns false when empty", () => {
      expect(hasCustomDimensions(makeConfig(), "request")).toBe(false);
    });

    it("returns true when matching phase exists", () => {
      const config = makeConfig({
        tone: {
          phase: "request",
          prompt: "test",
          output_schema: {},
        },
      });
      expect(hasCustomDimensions(config, "request")).toBe(true);
      expect(hasCustomDimensions(config, "final_answer")).toBe(false);
    });
  });

  describe("parseClassifierResponse", () => {
    it("returns all fields as standard when no custom dims", () => {
      const response = { intent: "code", complexity: 3 };
      const result = parseClassifierResponse(response, makeConfig());

      expect(result.standard).toEqual(response);
      expect(result.custom.size).toBe(0);
    });

    it("separates custom dimension fields from standard", () => {
      const config = makeConfig({
        safety: {
          phase: "final_answer",
          prompt: "test",
          output_schema: { safe: "boolean" },
        },
      });
      const response = {
        intent: "code",
        safety: { safe: true, confidence: 0.95 },
      };
      const result = parseClassifierResponse(response, config);

      expect(result.standard).toEqual({ intent: "code" });
      expect(result.custom.get("safety")).toEqual({
        safe: true,
        confidence: 0.95,
      });
    });

    it("wraps non-object custom values", () => {
      const config = makeConfig({
        score: {
          phase: "final_answer",
          prompt: "test",
          output_schema: { value: "number" },
        },
      });
      const response = { intent: "code", score: 42 };
      const result = parseClassifierResponse(response, config);

      expect(result.custom.get("score")).toEqual({ value: 42 });
    });
  });

  describe("extractCustomClassification", () => {
    it("returns undefined when no custom dims configured", () => {
      const result = extractCustomClassification(
        { intent: "code" },
        makeConfig(),
      );
      expect(result).toBeUndefined();
    });

    it("returns map when custom dims are present in response", () => {
      const config = makeConfig({
        safety: {
          phase: "final_answer",
          prompt: "test",
          output_schema: { safe: "boolean" },
        },
      });
      const result = extractCustomClassification(
        { safety: { safe: true } },
        config,
      );

      expect(result).toBeDefined();
      expect(result?.get("safety")).toEqual({ safe: true });
    });

    it("returns undefined when custom dims configured but not in response", () => {
      const config = makeConfig({
        safety: {
          phase: "final_answer",
          prompt: "test",
          output_schema: { safe: "boolean" },
        },
      });
      const result = extractCustomClassification(
        { intent: "code" },
        config,
      );

      expect(result).toBeUndefined();
    });
  });
});
