import {
  getRegisteredCapability,
  registerModelCapability,
  type ModelCapability,
} from "../llm/modelCapabilities.js";

export interface ModelCapabilityOverride {
  supportsThinking?: boolean;
  maxOutputTokens?: number;
  contextWindow?: number;
  inputUsdPerMtok?: number;
  outputUsdPerMtok?: number;
}

function positiveInteger(value: number | undefined, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return fallback;
  }
  return Math.floor(value);
}

function nonNegativeNumber(value: number | undefined, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return fallback;
  }
  return value;
}

export function registerConfiguredModelCapability(
  model: string,
  override: ModelCapabilityOverride | undefined,
): void {
  if (!override) return;
  const existing = getRegisteredCapability(model);
  const capability: ModelCapability = {
    id: model,
    supportsThinking: override.supportsThinking ?? existing?.supportsThinking ?? false,
    maxOutputTokens: positiveInteger(
      override.maxOutputTokens,
      existing?.maxOutputTokens ?? 8_192,
    ),
    contextWindow: positiveInteger(
      override.contextWindow,
      existing?.contextWindow ?? 200_000,
    ),
    inputUsdPerMtok: nonNegativeNumber(
      override.inputUsdPerMtok,
      existing?.inputUsdPerMtok ?? 0,
    ),
    outputUsdPerMtok: nonNegativeNumber(
      override.outputUsdPerMtok,
      existing?.outputUsdPerMtok ?? 0,
    ),
  };
  registerModelCapability(capability);
}
