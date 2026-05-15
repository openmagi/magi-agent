import { describe, it, expect } from "vitest";
import { PLAN_MAX_BOTS, PLAN_MONTHLY_CREDITS_CENTS, PLAN_RANK } from "./plans";

describe("PLAN_MAX_BOTS", () => {
  it("defines bot limits for all plans", () => {
    expect(PLAN_MAX_BOTS).toEqual({
      byok: 1,
      pro: 1,
      pro_plus: 1,
      max: 5,
      flex: 10,
    });
  });

  it("every plan in PLAN_RANK has a maxBots entry", () => {
    for (const plan of Object.keys(PLAN_RANK)) {
      expect(PLAN_MAX_BOTS[plan]).toBeGreaterThanOrEqual(1);
    }
  });
});

describe("PLAN_MONTHLY_CREDITS_CENTS", () => {
  it("grants only the LLM credit allowance after managed hosting cost", () => {
    expect(PLAN_MONTHLY_CREDITS_CENTS).toMatchObject({
      pro: 500,
      pro_plus: 8000,
      max: 35000,
      flex: 190000,
    });
  });
});
