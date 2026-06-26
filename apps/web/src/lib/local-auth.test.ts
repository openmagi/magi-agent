import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getLocalAccessToken,
  loadLocalBootstrap,
  resetLocalBootstrapCacheForTests,
} from "./local-auth";
import type { LocalBootstrap } from "./local-auth";

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

describe("loadLocalBootstrap setup block", () => {
  function stubBootstrap(payload: LocalBootstrap, status = 200): void {
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify(payload), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    ) as unknown as typeof globalThis.fetch;
  }

  it("parses a bootstrap payload that includes the additive setup block", async () => {
    stubBootstrap({
      ok: true,
      agentUrl: "http://127.0.0.1:8080",
      tokenRequired: false,
      setup: {
        needed: true,
        hasProvider: false,
        providers: ["anthropic", "openai", "gemini", "fireworks", "openrouter"],
      },
    });

    const bootstrap = await loadLocalBootstrap();

    expect(bootstrap?.setup?.needed).toBe(true);
    expect(bootstrap?.setup?.hasProvider).toBe(false);
    expect(bootstrap?.setup?.providers).toContain("anthropic");
  });

  it("still parses an older bootstrap payload that omits setup (back-compat)", async () => {
    stubBootstrap({
      ok: true,
      agentUrl: "http://127.0.0.1:8080",
      tokenRequired: false,
    });

    const bootstrap = await loadLocalBootstrap();

    expect(bootstrap?.ok).toBe(true);
    expect(bootstrap?.setup).toBeUndefined();
  });

  it("returns null when the bootstrap fetch fails", async () => {
    stubBootstrap({ ok: false }, 500);

    const bootstrap = await loadLocalBootstrap();

    expect(bootstrap).toBeNull();
  });
});
