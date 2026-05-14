import { describe, it, expect } from "vitest";
import { isContextOverflowError, ContextOverflowError } from "./ContextOverflowError.js";

describe("isContextOverflowError", () => {
  it("returns true for http_413 regardless of message", () => {
    expect(isContextOverflowError("http_413", "anything")).toBe(true);
  });

  it("returns true for http_400 with overflow-like messages", () => {
    expect(isContextOverflowError("http_400", "prompt is too long")).toBe(true);
    expect(isContextOverflowError("http_400", "max_tokens_exceeded")).toBe(true);
    expect(isContextOverflowError("http_400", "context_length_exceeded")).toBe(true);
    expect(isContextOverflowError("http_400", "request entity too large")).toBe(true);
    expect(isContextOverflowError("http_400", "input is too long for this model")).toBe(true);
    expect(isContextOverflowError("http_400", "exceeds the context window")).toBe(true);
    expect(isContextOverflowError("http_400", "maximum context length")).toBe(true);
  });

  it("returns false for http_400 with non-overflow messages", () => {
    expect(isContextOverflowError("http_400", "invalid request")).toBe(false);
    expect(isContextOverflowError("http_400", "missing api key")).toBe(false);
  });

  it("returns false for non-400/413 codes", () => {
    expect(isContextOverflowError("http_500", "prompt is too long")).toBe(false);
    expect(isContextOverflowError("http_429", "rate limited")).toBe(false);
  });
});

describe("ContextOverflowError", () => {
  it("constructs with correct properties", () => {
    const err = new ContextOverflowError("http_413", "too large");
    expect(err.name).toBe("ContextOverflowError");
    expect(err.httpCode).toBe("http_413");
    expect(err.upstreamMessage).toBe("too large");
    expect(err.message).toContain("http_413");
    expect(err.message).toContain("too large");
    expect(err instanceof Error).toBe(true);
  });
});
