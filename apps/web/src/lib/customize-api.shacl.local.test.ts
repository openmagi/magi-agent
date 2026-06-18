import { describe, expect, it, vi } from "vitest";

// Mock React and local-api so we can import customize-api.ts
// without a full browser/Next.js environment (vitest runs in node).
vi.mock("react", () => ({
  useCallback: (fn: unknown) => fn,
  useEffect: () => {},
  useState: (init: unknown) => [init, () => {}],
}));
vi.mock("./local-api", () => ({
  useAgentFetch: () => async () => new Response(),
}));

// eslint-disable-next-line import/order -- mocks must precede the import
import { compileCustomRule } from "./customize-api";
import type { ShaclCompileResponse } from "./customize-api";

/** Build a minimal fetch mock that resolves with a given JSON body and HTTP status. */
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

/** Build a fetch mock that rejects (simulates a network error). */
function mockFetchNetworkError(
  message = "Network failure",
): (path: string, init?: RequestInit) => Promise<Response> {
  return vi.fn().mockRejectedValue(new Error(message));
}

describe("compileCustomRule — SHACL compile API client", () => {
  it("maps a successful compile response to ShaclCompileResponse", async () => {
    const serverResponse: ShaclCompileResponse = {
      ok: true,
      shapeTtl: "@prefix sh: <http://www.w3.org/ns/shacl#> .",
      review: { verdict: "aligned", issues: [], confidence: 0.95 },
      explanation: "The generated shape matches your intent.",
      previewCases: [
        { conforms: true, status: "PASS", violations: [] },
        { conforms: false, status: "FAIL", violations: [{ message: "value out of range" }] },
      ],
      previewTruncated: false,
    };

    const fetchMock = mockFetch(serverResponse);
    const result = await compileCustomRule(fetchMock, "cost must be positive", [
      { cost: 5 },
      { cost: -1 },
    ]);

    expect(result.ok).toBe(true);
    expect(result.shapeTtl).toBe(serverResponse.shapeTtl);
    expect(result.review).toEqual(serverResponse.review);
    expect(result.explanation).toBe(serverResponse.explanation);
    expect(result.previewCases).toHaveLength(2);
    expect(result.previewCases?.[0]).toEqual({ conforms: true, status: "PASS", violations: [] });
    expect(result.previewTruncated).toBe(false);
  });

  it("returns {ok:false, error} as-is when server reports a compile failure", async () => {
    const serverResponse: ShaclCompileResponse = {
      ok: false,
      error: "NL compiler could not parse the constraint.",
    };

    const fetchMock = mockFetch(serverResponse);
    const result = await compileCustomRule(fetchMock, "something unparseable");

    expect(result.ok).toBe(false);
    expect(result.error).toBe("NL compiler could not parse the constraint.");
    expect(result.shapeTtl).toBeUndefined();
    expect(result.review).toBeUndefined();
  });

  it("returns {ok:false, error} on HTTP error status and does NOT throw", async () => {
    const fetchMock = mockFetch({ detail: "Internal Server Error" }, 500);
    const result = await compileCustomRule(fetchMock, "some rule text");

    expect(result.ok).toBe(false);
    expect(typeof result.error).toBe("string");
    expect(result.error).toBeTruthy();
  });

  it("returns {ok:false, error} on network failure and does NOT throw", async () => {
    const fetchMock = mockFetchNetworkError("fetch failed");
    const result = await compileCustomRule(fetchMock, "some rule text");

    expect(result.ok).toBe(false);
    expect(result.error).toContain("fetch failed");
  });
});
