/**
 * ExtendedClassifier — appends custom YAML-defined dimensions to the
 * existing classifier system prompt. No additional LLM calls; custom
 * dimensions piggyback on the existing Haiku classifier request.
 *
 * When no custom dimensions are configured, this module is a pure
 * passthrough (zero overhead).
 */

import type { ClassifierConfig, CustomDimension } from "../config/MagiConfig.js";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface ExtendedClassifierResult {
  /** Standard classifier fields (pass-through). */
  standard: Record<string, unknown>;
  /** Custom dimension results, keyed by dimension name. */
  custom: ReadonlyMap<string, Record<string, unknown>>;
}

/* ------------------------------------------------------------------ */
/*  Prompt building                                                    */
/* ------------------------------------------------------------------ */

/**
 * Build the additional system prompt fragment for custom dimensions
 * that belong to the given phase. Returns an empty string when there
 * are no dimensions for the phase.
 */
export function buildCustomDimensionPrompt(
  config: ClassifierConfig,
  phase: "request" | "final_answer",
): string {
  const dims = Object.entries(config.custom_dimensions).filter(
    ([, d]) => d.phase === phase,
  );
  if (dims.length === 0) return "";

  const blocks = dims.map(([name, dim]) => {
    const schemaLines = Object.entries(dim.output_schema)
      .map(([k, v]) => `    "${k}": ${v}`)
      .join(",\n");

    return [
      `## Custom Dimension: ${name}`,
      dim.prompt,
      `Output this dimension in the JSON response under the key "${name}" with schema:`,
      `  {`,
      schemaLines,
      `  }`,
    ].join("\n");
  });

  return "\n\n" + blocks.join("\n\n");
}

/**
 * Check whether any custom dimensions are configured for a given phase.
 */
export function hasCustomDimensions(
  config: ClassifierConfig,
  phase: "request" | "final_answer",
): boolean {
  return Object.values(config.custom_dimensions).some(
    (d) => d.phase === phase,
  );
}

/* ------------------------------------------------------------------ */
/*  Response parsing                                                   */
/* ------------------------------------------------------------------ */

/**
 * Parse a classifier JSON response that may contain custom dimension
 * fields. Standard fields are returned as-is; custom dimension fields
 * (matching configured dimension names) are extracted into the `custom`
 * map.
 *
 * When no custom dimensions are configured, `custom` is an empty map
 * and the full response is returned as `standard`.
 */
export function parseClassifierResponse(
  response: Record<string, unknown>,
  config: ClassifierConfig,
): ExtendedClassifierResult {
  const customDimNames = new Set(Object.keys(config.custom_dimensions));
  if (customDimNames.size === 0) {
    return {
      standard: response,
      custom: new Map(),
    };
  }

  const standard: Record<string, unknown> = {};
  const custom = new Map<string, Record<string, unknown>>();

  for (const [key, value] of Object.entries(response)) {
    if (customDimNames.has(key)) {
      if (value && typeof value === "object" && !Array.isArray(value)) {
        custom.set(key, value as Record<string, unknown>);
      } else {
        // Non-object custom dim value — wrap it
        custom.set(key, { value });
      }
    } else {
      standard[key] = value;
    }
  }

  return { standard, custom };
}

/**
 * Create a ReadonlyMap of custom classification results from a raw
 * classifier response. This is the convenience method that hook
 * context population should call.
 *
 * Returns `undefined` when no custom dimensions are configured
 * (signaling the HookContext field should remain absent).
 */
export function extractCustomClassification(
  response: Record<string, unknown>,
  config: ClassifierConfig,
): ReadonlyMap<string, Record<string, unknown>> | undefined {
  if (Object.keys(config.custom_dimensions).length === 0) return undefined;
  const { custom } = parseClassifierResponse(response, config);
  return custom.size > 0 ? custom : undefined;
}
