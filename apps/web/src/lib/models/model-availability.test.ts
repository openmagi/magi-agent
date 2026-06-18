import { describe, expect, it } from "vitest";
import {
  filterModelOptionsByConfiguredProviders,
  type ModelOptionLike,
} from "./model-availability";

// Inline a representative slice of BASE_MODEL_OPTIONS so this test stays free of
// the `@/`-aliased import chain that model-options.ts pulls in.
const OPTIONS: ModelOptionLike[] = [
  { value: "magi_smart_routing", label: "Open Magi Router" },
  { value: "sonnet", label: "Claude Sonnet" },
  { value: "gpt_5_5", label: "GPT-5.5" },
  { value: "gemini_3_1_pro", label: "Gemini 3.1 Pro" },
  { value: "kimi_k2_5", label: "Kimi K2.6" },
  { value: "minimax_m2_7", label: "MiniMax M2.7" },
];

describe("filterModelOptionsByConfiguredProviders", () => {
  it("keeps only options whose provider has a configured key", () => {
    const values = filterModelOptionsByConfiguredProviders(
      OPTIONS,
      new Set(["fireworks"]),
    ).map((o) => o.value);
    expect(values).toContain("kimi_k2_5");
    expect(values).toContain("minimax_m2_7");
    expect(values).not.toContain("sonnet");
    expect(values).not.toContain("gpt_5_5");
    expect(values).not.toContain("gemini_3_1_pro");
  });

  it("treats google alias as gemini", () => {
    const values = filterModelOptionsByConfiguredProviders(
      OPTIONS,
      new Set(["google"]),
    ).map((o) => o.value);
    expect(values).toContain("gemini_3_1_pro");
    expect(values).not.toContain("sonnet");
  });

  it("always keeps provider-agnostic router options", () => {
    const values = filterModelOptionsByConfiguredProviders(
      OPTIONS,
      new Set(["fireworks"]),
    ).map((o) => o.value);
    expect(values).toContain("magi_smart_routing");
  });

  it("fails open: empty configured set returns all options unchanged", () => {
    expect(filterModelOptionsByConfiguredProviders(OPTIONS, new Set())).toEqual(
      OPTIONS,
    );
  });

  it("never drops the currently-selected option even if unconfigured", () => {
    const values = filterModelOptionsByConfiguredProviders(
      OPTIONS,
      new Set(["fireworks"]),
      "gpt_5_5",
    ).map((o) => o.value);
    expect(values).toContain("gpt_5_5");
  });
});
