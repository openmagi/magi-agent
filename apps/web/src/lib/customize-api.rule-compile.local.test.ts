import { describe, expect, it, vi } from "vitest";

vi.mock("react", () => ({
  useCallback: (fn: unknown) => fn,
  useEffect: () => {},
  useState: (init: unknown) => [init, () => {}],
}));
vi.mock("./local-api", () => ({
  useAgentFetch: () => async () => new Response(),
}));

// eslint-disable-next-line import/order -- mocks must precede the import
import { compileRule, type RuleCompileResponse } from "./customize-api";

function mockFetch(
  body: unknown,
  status = 200,
): (path: string, init?: RequestInit) => Promise<Response> {
  const ok = status >= 200 && status < 300;
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response);
}


describe("compileRule — POST /v1/app/customize/rules/compile (PR-D1/D2)", () => {
  it("posts nlText only when priorTurns is empty", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ ok: true } as RuleCompileResponse),
    } as unknown as Response);
    await compileRule(fetch as unknown as typeof globalThis.fetch, "deny shell_exec");
    const [path, init] = (
      fetch as unknown as { mock: { calls: [string, RequestInit][] } }
    ).mock.calls[0];
    expect(path).toBe("/v1/app/customize/rules/compile");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      nlText: "deny shell_exec",
    });
  });

  it("includes priorTurns when non-empty", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ ok: true } as RuleCompileResponse),
    } as unknown as Response);
    await compileRule(fetch as unknown as typeof globalThis.fetch, "policy", [
      { role: "user", content: "earlier" },
    ]);
    const [, init] = (
      fetch as unknown as { mock: { calls: [string, RequestInit][] } }
    ).mock.calls[0];
    expect(JSON.parse(init.body as string).priorTurns).toEqual([
      { role: "user", content: "earlier" },
    ]);
  });

  it("passes the flag-OFF 200 response body through unchanged", async () => {
    const out = await compileRule(
      mockFetch({ ok: false, error: "nl-rule compiler disabled" }, 200),
      "policy",
    );
    expect(out.ok).toBe(false);
    expect(out.error).toBe("nl-rule compiler disabled");
  });

  it("returns {ok: false, error} on a non-2xx HTTP status without throwing", async () => {
    const out = await compileRule(
      mockFetch({ error: "boom" }, 500),
      "policy",
    );
    expect(out.ok).toBe(false);
    expect(out.error).toBe("boom");
  });

  it("returns {ok: false, error} on a network error without throwing", async () => {
    const fetch = vi.fn().mockRejectedValue(new Error("offline")) as unknown as typeof globalThis.fetch;
    const out = await compileRule(fetch, "p");
    expect(out.ok).toBe(false);
    expect(out.error).toBe("offline");
  });

  it("passes the full success payload through unchanged", async () => {
    const payload: RuleCompileResponse = {
      ok: true,
      routedKind: "tool_perm",
      draft: {
        scope: "always",
        enabled: true,
        firesAt: "before_tool_use",
        action: "block",
        what: {
          kind: "tool_perm",
          payload: { match: { tool: "shell_exec" }, decision: "deny" },
        },
      },
      explanation: "Before the agent calls a tool, deny shell_exec.",
      review: { verdict: "aligned", issues: [], confidence: 0.9 },
      schemaIssues: [],
    };
    const out = await compileRule(mockFetch(payload), "deny shell_exec");
    expect(out).toEqual(payload);
  });
});
