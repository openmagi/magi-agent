import { describe, expect, it } from "vitest";
import {
  isPlaintext,
  PLAINTEXT_PREFIX,
  unwrapPlaintext,
  wrapPlaintext,
} from "./plaintext-sentinel";

describe("plaintext-sentinel", () => {
  it("wraps content with the sentinel prefix", () => {
    expect(wrapPlaintext("hello")).toBe("plaintext:v1:hello");
  });

  it("round-trips: wrap → isPlaintext → unwrap", () => {
    const wrapped = wrapPlaintext("hello world");
    expect(isPlaintext(wrapped)).toBe(true);
    expect(unwrapPlaintext(wrapped)).toBe("hello world");
  });

  it("isPlaintext returns false for legacy ciphertext", () => {
    expect(isPlaintext("AAABBBCCC")).toBe(false);
  });

  it("isPlaintext returns false for empty string", () => {
    expect(isPlaintext("")).toBe(false);
  });

  it("unwrapPlaintext removes only the prefix length", () => {
    const content = "some:content:with:colons";
    expect(unwrapPlaintext(wrapPlaintext(content))).toBe(content);
  });

  it("PLAINTEXT_PREFIX is the expected literal", () => {
    expect(PLAINTEXT_PREFIX).toBe("plaintext:v1:");
  });

  // base64/hex ciphertext uses only [A-Za-z0-9+/=] or hex chars — neither
  // alphabet includes ":", so ciphertext can never start with "plaintext:v1:".
  it("unwrapPlaintext returns input unchanged when not prefixed (guard)", () => {
    expect(unwrapPlaintext("not-prefixed")).toBe("not-prefixed");
  });

  it("unwrapPlaintext round-trips wrapPlaintext of empty string to empty string", () => {
    expect(unwrapPlaintext(wrapPlaintext(""))).toBe("");
  });
});
