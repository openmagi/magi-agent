import { describe, expect, it } from "vitest";

import {
  CONSULTATION_ASR_CENTS_PER_MINUTE,
  estimateConsultationCreditsCents,
  roundDurationToBillableMinutes,
} from "./billing";

describe("consultation billing", () => {
  it("rounds positive durations up to whole minutes", () => {
    expect(roundDurationToBillableMinutes(1)).toBe(1);
    expect(roundDurationToBillableMinutes(60)).toBe(1);
    expect(roundDurationToBillableMinutes(61)).toBe(2);
    expect(roundDurationToBillableMinutes(0)).toBe(0);
  });

  it("estimates cents from billable minutes", () => {
    expect(estimateConsultationCreditsCents(61)).toBe(
      2 * CONSULTATION_ASR_CENTS_PER_MINUTE,
    );
  });

  it("allows caller-provided per-minute pricing", () => {
    expect(estimateConsultationCreditsCents(120, 7)).toBe(14);
  });
});
