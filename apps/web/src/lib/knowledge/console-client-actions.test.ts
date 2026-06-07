import { afterEach, describe, expect, it, vi } from "vitest";
import {
  deleteKnowledgeConsoleDocuments,
  downloadKnowledgeDocument,
  uploadKnowledgeConsoleFiles,
} from "./console-client-actions";
import type { KnowledgeConsoleDocument } from "./console-model";

const documentRow: KnowledgeConsoleDocument = {
  id: "doc-1",
  filename: "르챔버 2025 매출.xlsx",
  original_size: 81_300,
  object_key_original: "original/doc-1.xlsx",
  object_key_converted: "converted/doc-1.md",
  chunk_count: 1,
  status: "ready",
  created_at: "2026-04-25T00:00:00Z",
};

describe("downloadKnowledgeDocument", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("attaches and removes the generated anchor so browser downloads are triggered reliably", async () => {
    const anchor = {
      href: "",
      download: "",
      click: vi.fn(),
      remove: vi.fn(),
    };
    const appendChild = vi.fn();
    const createObjectURL = vi.fn(() => "blob:kb-download");
    const revokeObjectURL = vi.fn();
    const setTimeout = vi.fn((callback: () => void) => {
      callback();
      return 1;
    });

    vi.stubGlobal("window", {
      document: {
        createElement: vi.fn(() => anchor),
        body: { appendChild },
      },
      setTimeout,
    });
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });

    await downloadKnowledgeDocument({
      fetcher: async () =>
        new Response("file", {
          headers: {
            "Content-Disposition": "attachment; filename=\"%EB%A5%B4%EC%B1%94%EB%B2%84.xlsx\"",
          },
        }),
      scope: "personal",
      botId: "bot-1",
      document: documentRow,
      type: "original",
    });

    expect(anchor.href).toBe("blob:kb-download");
    expect(anchor.download).toBe("르챔버.xlsx");
    expect(appendChild).toHaveBeenCalledWith(anchor);
    expect(anchor.click).toHaveBeenCalledOnce();
    expect(setTimeout).toHaveBeenCalledOnce();
    expect(anchor.remove).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:kb-download");
  });
});

describe("uploadKnowledgeConsoleFiles", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("sends the collection name when preparing a personal upload URL", async () => {
    const fetcher = vi.fn(async (url: string) => {
      if (url === "/api/knowledge/upload-url") {
        return new Response(
          JSON.stringify({
            upload_url: "https://upload.test/file",
            storage_path: "knowledge/bot-2/file.md",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url === "/api/knowledge/upload") {
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }

      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 200 })));

    await uploadKnowledgeConsoleFiles({
      fetcher,
      scope: "personal",
      collectionName: "Research",
      files: [new File(["notes"], "notes.md", { type: "text/markdown" })],
    });

    const uploadUrlCall = fetcher.mock.calls.find(([url]) => url === "/api/knowledge/upload-url");
    expect(uploadUrlCall).toBeTruthy();
    expect(JSON.parse(uploadUrlCall?.[1]?.body as string)).toMatchObject({
      collection: "Research",
      filename: "notes.md",
    });
  });

  it("reports per-file upload and indexing progress", async () => {
    const progressUpdates: Array<{ index: number; filename: string; phase: string; progress: number }> = [];
    const fetcher = vi.fn(async (url: string) => {
      if (url === "/api/knowledge/upload-url") {
        return new Response(
          JSON.stringify({
            upload_url: "https://upload.test/file",
            storage_path: "knowledge/bot-2/file.md",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url === "/api/knowledge/upload") {
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }

      return new Response(null, { status: 404 });
    });

    class MockXMLHttpRequest {
      status = 200;
      uploadProgressListener: ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null = null;
      loadListener: (() => void) | null = null;
      upload = {
        addEventListener: (eventName: string, listener: (event: { lengthComputable: boolean; loaded: number; total: number }) => void) => {
          if (eventName === "progress") this.uploadProgressListener = listener;
        },
      };

      addEventListener(eventName: string, listener: () => void) {
        if (eventName === "load") this.loadListener = listener;
      }

      open() {}
      setRequestHeader() {}
      send() {
        this.uploadProgressListener?.({ lengthComputable: true, loaded: 25, total: 100 });
        this.uploadProgressListener?.({ lengthComputable: true, loaded: 100, total: 100 });
        this.loadListener?.();
      }
    }

    vi.stubGlobal("XMLHttpRequest", MockXMLHttpRequest);

    const result = await uploadKnowledgeConsoleFiles({
      fetcher,
      scope: "personal",
      collectionName: "Research",
      files: [new File(["notes"], "notes.md", { type: "text/markdown" })],
      onProgress: (update) => progressUpdates.push(update),
    });

    expect(result.uploaded).toBe(1);
    expect(progressUpdates).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ index: 0, filename: "notes.md", phase: "uploading" }),
        expect.objectContaining({ index: 0, filename: "notes.md", phase: "indexing", progress: 90 }),
        expect.objectContaining({ index: 0, filename: "notes.md", phase: "ready", progress: 100 }),
      ]),
    );
    expect(
      progressUpdates.some((update) => update.phase === "uploading" && update.progress > 0 && update.progress < 90),
    ).toBe(true);
  });
});

describe("deleteKnowledgeConsoleDocuments", () => {
  it("sends every selected document id in one batch delete request", async () => {
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify({ deleted: 2, failures: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await deleteKnowledgeConsoleDocuments({
      fetcher,
      scope: "personal",
      botId: "bot-1",
      documents: [
        documentRow,
        { ...documentRow, id: "doc-2", filename: "르챔버 2024 매출.xlsx" },
      ],
    });

    expect(result).toEqual({ deleted: 2, failures: [] });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith(
      "/api/knowledge/documents/batch-delete",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );
    expect(JSON.parse(fetcher.mock.calls[0]?.[1]?.body as string)).toEqual({
      botId: "bot-1",
      doc_ids: ["doc-1", "doc-2"],
    });
  });
});
