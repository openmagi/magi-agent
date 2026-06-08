import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { agentFetch } from "./local-api";
import { resetLocalBootstrapCacheForTests } from "./local-auth";

describe("agentFetch", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.unstubAllEnvs();
    resetLocalBootstrapCacheForTests();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.unstubAllEnvs();
    resetLocalBootstrapCacheForTests();
  });

  it("uses same-origin local runtime URLs and bootstrap auth when no env base URL is set", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/app/bootstrap.json") {
        return new Response(JSON.stringify({ ok: true, token: "loopback-token" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as typeof fetch;

    await agentFetch("/v1/app/config");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/app/bootstrap.json",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/v1/app/config",
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );
    const [, options] = fetchMock.mock.calls[1]!;
    expect((options?.headers as Headers).get("Authorization")).toBe("Bearer loopback-token");
    expect((options?.headers as Headers).get("x-gateway-token")).toBe("loopback-token");
  });

  it("honors explicit env URL and token overrides", async () => {
    vi.stubEnv("NEXT_PUBLIC_AGENT_URL", "http://127.0.0.1:9090/");
    vi.stubEnv("NEXT_PUBLIC_AGENT_TOKEN", "env-token");
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    globalThis.fetch = fetchMock as typeof fetch;

    await agentFetch("/v1/app/skills");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:9090/v1/app/skills",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    const [, options] = fetchMock.mock.calls[0]!;
    expect((options?.headers as Headers).get("Authorization")).toBe("Bearer env-token");
    expect((options?.headers as Headers).get("x-gateway-token")).toBe("env-token");
  });

  it("preserves caller-provided auth headers", async () => {
    vi.stubEnv("NEXT_PUBLIC_AGENT_TOKEN", "env-token");
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    globalThis.fetch = fetchMock as typeof fetch;

    await agentFetch("/v1/admin/tools", {
      headers: {
        Authorization: "Bearer custom-bearer",
        "x-gateway-token": "custom-gateway",
      },
    });

    const [, options] = fetchMock.mock.calls[0]!;
    expect((options?.headers as Headers).get("Authorization")).toBe("Bearer custom-bearer");
    expect((options?.headers as Headers).get("x-gateway-token")).toBe("custom-gateway");
  });
});
