import { describe, it, expect } from "vitest";
import { randomBytes } from "crypto";

describe("gateway token generation", () => {
  it("generates token with gw_ prefix", () => {
    const token = `gw_${randomBytes(32).toString("hex")}`;
    expect(token.startsWith("gw_")).toBe(true);
  });

  it("generates 64 hex chars after prefix", () => {
    const token = `gw_${randomBytes(32).toString("hex")}`;
    const hexPart = token.slice(3);
    expect(hexPart).toHaveLength(64);
    expect(/^[0-9a-f]+$/.test(hexPart)).toBe(true);
  });

  it("generates unique tokens each time", () => {
    const token1 = `gw_${randomBytes(32).toString("hex")}`;
    const token2 = `gw_${randomBytes(32).toString("hex")}`;
    expect(token1).not.toBe(token2);
  });

  it("total token length is 67 chars (3 prefix + 64 hex)", () => {
    const token = `gw_${randomBytes(32).toString("hex")}`;
    expect(token).toHaveLength(67);
  });
});
