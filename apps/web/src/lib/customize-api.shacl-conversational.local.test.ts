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
import type { ConversationTurn, ShaclCompileResponse } from "./customize-api";

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

/**
 * Extract the parsed JSON body from a vi.fn() fetch mock call.
 * The body was passed as the second argument's `body` property (a JSON string).
 */
function getRequestBody(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const init: RequestInit = fetchMock.mock.calls[0][1] as RequestInit;
  return JSON.parse(init.body as string) as Record<string, unknown>;
}

describe("compileCustomRule — conversational priorTurns extension", () => {
  // Test 1: priorTurns=undefined → body does NOT include priorTurns key
  it("omits priorTurns from the request body when called with undefined (regression: existing callers unaffected)", async () => {
    const fetchMock = mockFetch({ ok: true, shapeTtl: "@prefix sh: <...> ." });
    await compileCustomRule(fetchMock as typeof fetch, "hello", undefined, undefined);

    const body = getRequestBody(fetchMock as ReturnType<typeof vi.fn>);
    expect(Object.prototype.hasOwnProperty.call(body, "priorTurns")).toBe(false);
  });

  // Test 2: priorTurns=[] → body does NOT include priorTurns (empty array treated as omitted)
  it("omits priorTurns from the request body when called with an empty array", async () => {
    const fetchMock = mockFetch({ ok: true, shapeTtl: "@prefix sh: <...> ." });
    await compileCustomRule(fetchMock as typeof fetch, "hello", undefined, []);

    const body = getRequestBody(fetchMock as ReturnType<typeof vi.fn>);
    expect(Object.prototype.hasOwnProperty.call(body, "priorTurns")).toBe(false);
  });

  // Test 3: priorTurns with entries → body INCLUDES priorTurns verbatim
  it("includes priorTurns verbatim in the request body when non-empty", async () => {
    const fetchMock = mockFetch({ ok: true, shapeTtl: "@prefix sh: <...> ." });
    const turns: ConversationTurn[] = [
      { role: "user", content: "X" },
      { role: "assistant", content: "Y" },
    ];
    await compileCustomRule(fetchMock as typeof fetch, "more details", undefined, turns);

    const body = getRequestBody(fetchMock as ReturnType<typeof vi.fn>);
    expect(body.priorTurns).toEqual(turns);
  });

  // Test 4: clarifyingQuestions response is preserved exactly
  it("preserves clarifyingQuestions and explicit null error from the server response", async () => {
    const serverResponse: ShaclCompileResponse = {
      ok: false,
      clarifyingQuestions: ["q1", "q2"],
      shapeTtl: undefined,
      error: null,
    };
    const fetchMock = mockFetch(serverResponse);
    const result = await compileCustomRule(fetchMock as typeof fetch, "ambiguous rule");

    expect(result.ok).toBe(false);
    expect(result.clarifyingQuestions).toEqual(["q1", "q2"]);
    expect(result.shapeTtl).toBeUndefined();
    expect(result.error).toBeNull();
  });

  // Test 5: existing happy-path still passes (regression)
  it("maps a successful compile response correctly (existing happy-path regression)", async () => {
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
    const result = await compileCustomRule(fetchMock as typeof fetch, "cost must be positive", [
      { cost: 5 },
      { cost: -1 },
    ]);

    expect(result.ok).toBe(true);
    expect(result.shapeTtl).toBe(serverResponse.shapeTtl);
    expect(result.review).toEqual(serverResponse.review);
    expect(result.explanation).toBe(serverResponse.explanation);
    expect(result.previewCases).toHaveLength(2);
    expect(result.previewTruncated).toBe(false);
  });

  // Test 6: network error → returns {ok:false, error}, does NOT throw (regression)
  it("returns {ok:false, error} on network failure and does NOT throw", async () => {
    const fetchMock = mockFetchNetworkError("connection refused");
    const result = await compileCustomRule(fetchMock as typeof fetch, "some rule");

    expect(result.ok).toBe(false);
    expect(result.error).toContain("connection refused");
  });
});
