import { afterEach, describe, expect, it, vi } from "vitest";
import { getLocalAccessToken, resetLocalBootstrapCacheForTests } from "./local-auth";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  resetLocalBootstrapCacheForTests();
});

describe("getLocalAccessToken", () => {
  it("reads the loopback token from the local app bootstrap endpoint", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      agentUrl: "http://localhost:8080",
      tokenRequired: true,
      token: "local-token",
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    await expect(getLocalAccessToken()).resolves.toBe("local-token");
    expect(fetchMock).toHaveBeenCalledWith("/app/bootstrap.json", { cache: "no-store" });
  });
});
