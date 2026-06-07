import { describe, expect, it } from "vitest";
import {
  applyRouterPickerMode,
  getRouterDisplayName,
  getRouterPickerMode,
} from "./router-tier";

describe("router-tier model picker helpers", () => {
  it("maps persisted standard and premium router rows to compact picker modes", () => {
    expect(getRouterPickerMode("clawy_smart_routing", "standard")).toBe("standard_router");
    expect(getRouterPickerMode("clawy_smart_routing", "big_dic")).toBe("premium_router");
    expect(getRouterPickerMode("opus", "standard")).toBe("advanced");
  });

  it("maps compact picker modes back to existing model_selection/router_type values", () => {
    expect(applyRouterPickerMode("standard_router", "opus")).toEqual({
      modelSelection: "clawy_smart_routing",
      routerType: "standard",
    });
    expect(applyRouterPickerMode("premium_router", "opus")).toEqual({
      modelSelection: "clawy_smart_routing",
      routerType: "big_dic",
    });
    expect(applyRouterPickerMode("advanced", "gpt_5_5")).toEqual({
      modelSelection: "gpt_5_5",
      routerType: "standard",
    });
    expect(applyRouterPickerMode("advanced", "smart_routing")).toEqual({
      modelSelection: "opus",
      routerType: "standard",
    });
  });

  it("uses product-facing router labels", () => {
    expect(getRouterDisplayName("clawy_smart_routing", "standard")).toBe("Standard Router");
    expect(getRouterDisplayName("clawy_smart_routing", "big_dic")).toBe("Premium Router");
    expect(getRouterDisplayName("opus", "standard")).toBe("Claude Opus 4.6");
  });
});
