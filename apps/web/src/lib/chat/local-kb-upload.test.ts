import { afterEach, describe, expect, it, vi } from "vitest";

import { uploadChatFilesToLocalKb } from "./local-kb-upload";
import type { PendingKbUpload } from "@/chat-core";

const agentFetch = vi.fn();
vi.mock("@/lib/local-api", () => ({
  agentFetch: (...args: unknown[]) => agentFetch(...args),
}));

function makeFile(name: string, body = "content"): File {
  return new File([body], name, { type: "" });
}

function okResponse(overrides: Record<string, unknown> = {}): Response {
  return {
    ok: true,
    json: async () => ({
      doc_id: `knowledge/Downloads/${overrides.filename ?? "report.pdf"}`,
      collection_id: "Downloads",
      collection: "Downloads",
      filename: overrides.filename ?? "report.pdf",
      mime_type: "application/pdf",
      status: "ready",
      ...overrides,
    }),
  } as unknown as Response;
}

afterEach(() => {
  agentFetch.mockReset();
});

describe("uploadChatFilesToLocalKb", () => {
  it("posts raw bytes with filename/collection headers and returns a ref", async () => {
    agentFetch.mockResolvedValue(okResponse());
    const file = makeFile("report.pdf");
    const updates: PendingKbUpload[] = [];

    const refs = await uploadChatFilesToLocalKb([file], (u) => updates.push(u));

    expect(agentFetch).toHaveBeenCalledTimes(1);
    const [path, init] = agentFetch.mock.calls[0];
    expect(path).toBe("/v1/app/knowledge/upload");
    expect(init.method).toBe("POST");
    expect(init.headers["x-filename"]).toBe(encodeURIComponent("report.pdf"));
    expect(init.headers["x-collection"]).toBe("Downloads");
    expect(init.headers["Content-Type"]).toBe("application/pdf");
    expect(init.body).toBe(file);

    expect(refs).toHaveLength(1);
    expect(refs[0].id).toBe("knowledge/Downloads/report.pdf");
    expect(refs[0].source).toBe("chat_upload");
    expect(updates.map((u) => u.phase)).toEqual(["uploading", "indexing", "ready"]);
  });

  it("emits a failed phase and throws when the runtime rejects", async () => {
    agentFetch.mockResolvedValue({
      ok: false,
      json: async () => ({ status: "error", error: "file_too_large" }),
    } as unknown as Response);
    const updates: PendingKbUpload[] = [];

    await expect(
      uploadChatFilesToLocalKb([makeFile("big.bin")], (u) => updates.push(u)),
    ).rejects.toThrow("file_too_large");
    expect(updates.at(-1)?.phase).toBe("failed");
  });

  it("uses a browser mime fallback for unknown extensions", async () => {
    agentFetch.mockResolvedValue(okResponse({ filename: "data.weird" }));
    const file = new File(["x"], "data.weird", { type: "application/x-custom" });

    await uploadChatFilesToLocalKb([file], () => {});

    expect(agentFetch.mock.calls[0][1].headers["Content-Type"]).toBe("application/x-custom");
  });
});
