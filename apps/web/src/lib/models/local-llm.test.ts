import { describe, expect, it } from "vitest";
import {
  LOCAL_LLM_MODEL_OPTIONS,
  canUseLocalLlmModel,
  getLocalLlmModel,
  getLocalLlmModelEntitlementError,
  isLocalLlmModel,
} from "./local-llm";

describe("local LLM model catalog", () => {
  it("defines the three Mac Studio model selections", () => {
    expect(LOCAL_LLM_MODEL_OPTIONS.map((model) => model.value)).toEqual([
      "local_gemma_fast",
      "local_gemma_max",
      "local_qwen_uncensored",
    ]);
  });

  it("maps DB selections to runtime and upstream IDs", () => {
    expect(getLocalLlmModel("local_gemma_fast")).toMatchObject({
      label: "Gemma 4 Fast (beta)",
      runtimeModel: "local/gemma-fast",
      upstreamModel: "gemma-fast",
    });
    expect(getLocalLlmModel("local_gemma_max")).toMatchObject({
      label: "Gemma 4 Max (beta)",
      runtimeModel: "local/gemma-max",
      upstreamModel: "gemma-max",
    });
    expect(getLocalLlmModel("local_qwen_uncensored")).toMatchObject({
      label: "Qwen 3.5 Uncensored (beta)",
      runtimeModel: "local/qwen-uncensored",
      upstreamModel: "qwen-uncensored",
    });
  });

  it("does not expose the host name in user-facing local model labels", () => {
    for (const option of LOCAL_LLM_MODEL_OPTIONS) {
      expect(option.label).not.toContain("Mac Studio");
      expect(option.description).not.toContain("Mac Studio");
      expect(option.label).toMatch(/^(Gemma 4|Qwen 3\.5)/);
      expect(option.label).toContain("(beta)");
    }
  });

  it("recognizes only local LLM model selections", () => {
    expect(isLocalLlmModel("local_gemma_fast")).toBe(true);
    expect(isLocalLlmModel("gpt_5_4")).toBe(false);
  });

  it("allows local models only for Max and Flex platform-credit users", () => {
    expect(canUseLocalLlmModel("local_gemma_fast", "platform_credits", "max")).toBe(true);
    expect(canUseLocalLlmModel("local_gemma_fast", "platform_credits", "flex")).toBe(true);
    expect(canUseLocalLlmModel("local_gemma_fast", "platform_credits", "pro_plus")).toBe(false);
    expect(canUseLocalLlmModel("local_gemma_fast", "byok", "flex")).toBe(false);
    expect(canUseLocalLlmModel("sonnet", "byok", "pro")).toBe(true);
  });

  it("returns stable entitlement error metadata", () => {
    expect(getLocalLlmModelEntitlementError("local_gemma_fast", "platform_credits", "pro")).toEqual({
      code: "local_llm_requires_max",
      message: "Local beta models are available on Max and Flex plans.",
      status: 403,
    });
    expect(getLocalLlmModelEntitlementError("local_gemma_fast", "byok", "flex")).toEqual({
      code: "local_llm_requires_platform_credits",
      message: "Local beta models use platform credits, not BYOK.",
      status: 403,
    });
    expect(getLocalLlmModelEntitlementError("sonnet", "byok", "pro")).toBeNull();
  });

  it("allows new Max checkout selection before a subscription exists", () => {
    expect(canUseLocalLlmModel("local_gemma_fast", "platform_credits", "max")).toBe(true);
  });

  it("blocks Pro checkout selection before a subscription exists", () => {
    expect(getLocalLlmModelEntitlementError("local_gemma_fast", "platform_credits", "pro")).toMatchObject({
      status: 403,
      code: "local_llm_requires_max",
    });
  });
});
