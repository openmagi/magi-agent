import { describe, expect, it } from "vitest";
import { normalizeTelegramPhoneInput } from "./phone";

describe("normalizeTelegramPhoneInput", () => {
  it("removes the Korean trunk prefix before adding +82", () => {
    expect(normalizeTelegramPhoneInput("+82", "010-1234-5678")).toBe("+821012345678");
    expect(normalizeTelegramPhoneInput("+82", "010 1234 5678")).toBe("+821012345678");
  });

  it("does not strip ordinary leading digits for NANP numbers", () => {
    expect(normalizeTelegramPhoneInput("+1", "415 555 0100")).toBe("+14155550100");
  });

  it("respects pasted international numbers instead of prefixing twice", () => {
    expect(normalizeTelegramPhoneInput("+82", "+82 10 1234 5678")).toBe("+821012345678");
    expect(normalizeTelegramPhoneInput("+82", "82 10 1234 5678")).toBe("+821012345678");
  });
});
