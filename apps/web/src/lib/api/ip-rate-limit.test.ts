import { describe, expect, it } from "vitest";

import {
  checkIpRateLimit,
  getClientIp,
  resetIpRateLimitForTests,
} from "./ip-rate-limit";

describe("ip-rate-limit", () => {
  it("uses the first forwarded IP and falls back to unknown", () => {
    const forwarded = new Request("https://clawy.test/api", {
      headers: { "x-forwarded-for": "203.0.113.10, 10.0.0.2" },
    });
    const missing = new Request("https://clawy.test/api");

    expect(getClientIp(forwarded)).toBe("203.0.113.10");
    expect(getClientIp(missing)).toBe("unknown");
  });

  it("blocks requests after the configured window budget is exhausted", () => {
    resetIpRateLimitForTests();
    const request = new Request("https://clawy.test/api", {
      headers: { "x-forwarded-for": "203.0.113.20" },
    });

    expect(checkIpRateLimit(request, {
      keyPrefix: "test",
      limit: 2,
      windowMs: 60_000,
    })).toMatchObject({ allowed: true, remaining: 1 });
    expect(checkIpRateLimit(request, {
      keyPrefix: "test",
      limit: 2,
      windowMs: 60_000,
    })).toMatchObject({ allowed: true, remaining: 0 });
    expect(checkIpRateLimit(request, {
      keyPrefix: "test",
      limit: 2,
      windowMs: 60_000,
    })).toMatchObject({ allowed: false, retryAfterSeconds: 60 });
  });
});
