import { describe, expect, it, vi } from "vitest";

import {
  deleteMode,
  getModes,
  putMode,
  setActiveMode,
} from "./agent-modes-api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const SAMPLE_MODE = {
  id: "coding",
  displayName: "Coding",
  systemPrompt: "Be careful.",
  toolDelta: { exclude: ["WebSearch"], include: [] },
  scopedPolicyIds: [],
};

describe("getModes", () => {
  it("returns the modes list and active id", async () => {
    const fetch = vi.fn().mockResolvedValue(
      jsonResponse({ modes: [SAMPLE_MODE], activeMode: "coding" }),
    );
    const data = await getModes(fetch);
    expect(fetch).toHaveBeenCalledWith("/v1/app/modes");
    expect(data.modes).toHaveLength(1);
    expect(data.activeMode).toBe("coding");
  });

  it("throws on a non-2xx response", async () => {
    const fetch = vi.fn().mockResolvedValue(jsonResponse({}, 500));
    await expect(getModes(fetch)).rejects.toThrow(/500/);
  });
});

describe("putMode", () => {
  it("PUTs to the id-scoped path and returns the reconciled list", async () => {
    const fetch = vi.fn().mockResolvedValue(
      jsonResponse({ mode: SAMPLE_MODE, modes: [SAMPLE_MODE], activeMode: null }),
    );
    const res = await putMode(fetch, "coding", { displayName: "Coding" });
    const [path, init] = fetch.mock.calls[0];
    expect(path).toBe("/v1/app/modes/coding");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).not.toHaveProperty("id");
    expect(res.mode.id).toBe("coding");
  });

  it("surfaces the backend error code on invalid mode", async () => {
    const fetch = vi.fn().mockResolvedValue(
      jsonResponse({ error: "invalid_mode" }, 400),
    );
    await expect(
      putMode(fetch, "coding", { displayName: "   " }),
    ).rejects.toThrow(/invalid_mode/);
  });

  it("url-encodes the mode id in the path", async () => {
    const fetch = vi.fn().mockResolvedValue(
      jsonResponse({ mode: SAMPLE_MODE, modes: [], activeMode: null }),
    );
    await putMode(fetch, "a b", { displayName: "x" });
    expect(fetch.mock.calls[0][0]).toBe("/v1/app/modes/a%20b");
  });
});

describe("deleteMode", () => {
  it("DELETEs and returns the reconciled list", async () => {
    const fetch = vi.fn().mockResolvedValue(
      jsonResponse({ modes: [], activeMode: null }),
    );
    const res = await deleteMode(fetch, "coding");
    expect(fetch.mock.calls[0][1].method).toBe("DELETE");
    expect(res.modes).toEqual([]);
  });
});

describe("setActiveMode", () => {
  it("POSTs the modeId and returns the active id", async () => {
    const fetch = vi.fn().mockResolvedValue(jsonResponse({ activeMode: "coding" }));
    const res = await setActiveMode(fetch, "coding");
    const [path, init] = fetch.mock.calls[0];
    expect(path).toBe("/v1/app/modes/active");
    expect(JSON.parse(init.body as string)).toEqual({ modeId: "coding" });
    expect(res.activeMode).toBe("coding");
  });

  it("sends modeId null to clear the active mode", async () => {
    const fetch = vi.fn().mockResolvedValue(jsonResponse({ activeMode: null }));
    await setActiveMode(fetch, null);
    expect(JSON.parse(fetch.mock.calls[0][1].body as string)).toEqual({ modeId: null });
  });

  it("surfaces the backend error on unknown mode", async () => {
    const fetch = vi.fn().mockResolvedValue(
      jsonResponse({ error: "unknown_mode" }, 404),
    );
    await expect(setActiveMode(fetch, "nope")).rejects.toThrow(/unknown_mode/);
  });
});
