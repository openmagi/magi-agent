import { describe, expect, it } from "vitest";
import {
  MAGI_MANAGED_MODEL_OPTION,
  MAGI_MANAGED_MODEL_VALUE,
  getModelOptions,
  isMagiManagedModel,
} from "./model-options";
import { filterModelOptionsByConfiguredProviders } from "./model-availability";

describe("Magi managed model option", () => {
  it("is excluded by default", () => {
    const values = getModelOptions(null).map((o) => o.value);
    expect(values).not.toContain(MAGI_MANAGED_MODEL_VALUE);
  });

  it("is prepended when includeManagedInference is set", () => {
    const options = getModelOptions(null, { includeManagedInference: true });
    expect(options[0]).toEqual(MAGI_MANAGED_MODEL_OPTION);
    // Base models still present after it.
    expect(options.some((o) => o.value === "glm_5_2")).toBe(true);
  });

  it("isMagiManagedModel recognises only the managed value", () => {
    expect(isMagiManagedModel(MAGI_MANAGED_MODEL_VALUE)).toBe(true);
    expect(isMagiManagedModel("glm_5_2")).toBe(false);
    expect(isMagiManagedModel(null)).toBe(false);
    expect(isMagiManagedModel(undefined)).toBe(false);
  });

  it("survives the key-availability filter (provider-agnostic, no BYO key needed)", () => {
    const options = getModelOptions(null, { includeManagedInference: true });
    // Only anthropic configured — managed must still show; glm (fireworks) drops.
    const filtered = filterModelOptionsByConfiguredProviders(
      options,
      new Set(["anthropic"]),
      "sonnet",
    );
    expect(filtered.some((o) => o.value === MAGI_MANAGED_MODEL_VALUE)).toBe(true);
    expect(filtered.some((o) => o.value === "glm_5_2")).toBe(false);
  });
});
