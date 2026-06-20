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
import {
  compileSeamSpec,
  deleteSeamSpec,
  putSeamSpec,
  type SeamSpecCompileResponse,
} from "./customize-api";

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


describe("compileSeamSpec — POST /v1/app/customize/seams/compile", () => {
  it("posts nlText only when priorTurns is empty (wire byte-identical to legacy callers)", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ ok: true, spec: null, review: { verdict: "unknown", issues: [], confidence: 0 }, schemaIssues: [] }),
    } as unknown as Response);
    await compileSeamSpec(fetch as unknown as typeof globalThis.fetch, "any policy");
    const [path, init] = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0];
    expect(path).toBe("/v1/app/customize/seams/compile");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ nlText: "any policy" });
  });

  it("includes priorTurns in the body when non-empty", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ ok: true } as SeamSpecCompileResponse),
    } as unknown as Response);
    await compileSeamSpec(fetch as unknown as typeof globalThis.fetch, "p", [
      { role: "user", content: "earlier" },
    ]);
    const [, init] = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0];
    expect(JSON.parse(init.body as string).priorTurns).toEqual([{ role: "user", content: "earlier" }]);
  });

  it("returns the SeamSpecCompileResponse on success", async () => {
    const payload: SeamSpecCompileResponse = {
      ok: true,
      spec: { spec_version: "0.1", actions: [{ op: "modify_seam", preset_id: "x", wiring: "opt_in" }] },
      review: { verdict: "aligned", issues: [], confidence: 0.9 },
      schemaIssues: [],
    };
    const out = await compileSeamSpec(mockFetch(payload), "policy");
    expect(out).toEqual(payload);
  });

  it("returns {ok: false, error} on non-OK HTTP status without throwing", async () => {
    const out = await compileSeamSpec(mockFetch({ error: "boom" }, 500), "p");
    expect(out.ok).toBe(false);
    expect(out.error).toBe("boom");
  });

  it("returns {ok: false, error} on network error without throwing", async () => {
    const fetch = vi.fn().mockRejectedValue(new Error("offline")) as unknown as typeof globalThis.fetch;
    const out = await compileSeamSpec(fetch, "p");
    expect(out.ok).toBe(false);
    expect(out.error).toBe("offline");
  });
});


describe("putSeamSpec — PUT /v1/app/customize/seams", () => {
  it("PUTs the doc and returns {id, overrides} on success", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ id: "seam_a", overrides: { verification: { seam_specs: [] } } }),
    } as unknown as Response);
    const res = await putSeamSpec(fetch as unknown as typeof globalThis.fetch, {
      spec_version: "0.1",
      actions: [],
    });
    expect(res.id).toBe("seam_a");
    const [path, init] = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0];
    expect(path).toBe("/v1/app/customize/seams");
    expect(init.method).toBe("PUT");
  });

  it("throws with the joined schemaIssues on a 422 response", async () => {
    const fetch = mockFetch(
      { error: "invalid spec", schemaIssues: ["issue A", "issue B"] },
      422,
    );
    await expect(
      putSeamSpec(fetch, { spec_version: "0.1", actions: [] }),
    ).rejects.toThrow(/issue A.*issue B/);
  });

  it("throws on other non-OK statuses", async () => {
    const fetch = mockFetch({ error: "boom" }, 500);
    await expect(
      putSeamSpec(fetch, { spec_version: "0.1", actions: [] }),
    ).rejects.toThrow(/500/);
  });
});


describe("deleteSeamSpec — DELETE /v1/app/customize/seams/{id}", () => {
  it("DELETEs the encoded id and returns the new overrides", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ overrides: { verification: { seam_specs: [] } } }),
    } as unknown as Response);
    const out = await deleteSeamSpec(fetch as unknown as typeof globalThis.fetch, "seam a");
    expect(out).toBeTruthy();
    const [path, init] = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0];
    expect(path).toBe("/v1/app/customize/seams/seam%20a");
    expect(init.method).toBe("DELETE");
  });

  it("throws on non-OK status", async () => {
    await expect(deleteSeamSpec(mockFetch({}, 500), "x")).rejects.toThrow(/500/);
  });
});
