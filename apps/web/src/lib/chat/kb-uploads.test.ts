import { beforeEach, describe, expect, it, vi } from "vitest";
import { uploadChatFilesToKb, splitImageAndOtherFiles } from "./kb-uploads";
import type { PendingKbUpload } from "./kb-uploads";

describe("uploadChatFilesToKb", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("ensures Downloads, uploads to storage, and returns KB refs for chat", async () => {
    const updates: PendingKbUpload[] = [];
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            collection: { id: "col-1", name: "Downloads" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            upload_url: "https://upload.test/knowledge",
            storage_path: "knowledge/bot-1/171234_photo.png",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            doc_id: "doc-1",
            collection_id: "col-1",
            collection: "Downloads",
            filename: "photo.png",
            mime_type: "image/png",
            status: "ready",
            object_key_original: "bot-1/Downloads/original/photo.png",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["image"], "photo.png", { type: "image/png" });

    const refs = await uploadChatFilesToKb("bot-1", [file], (update) => updates.push(update));

    expect(refs).toEqual([
      expect.objectContaining({
        id: "doc-1",
        filename: "photo.png",
        collectionId: "col-1",
        collectionName: "Downloads",
        mimeType: "image/png",
        source: "chat_upload",
      }),
    ]);
    expect(updates.map((update) => update.phase)).toEqual(["uploading", "indexing", "ready"]);
    expect(updates[2]?.ref).toEqual(refs[0]);
  });

  it("emits a failed phase when indexing fails", async () => {
    const updates: PendingKbUpload[] = [];
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            collection: { id: "col-1", name: "Downloads" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            upload_url: "https://upload.test/knowledge",
            storage_path: "knowledge/bot-1/171234_notes.txt",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: "OCR failed",
          }),
          { status: 500, headers: { "Content-Type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["note"], "notes.txt", { type: "text/plain" });

    await expect(
      uploadChatFilesToKb("bot-1", [file], (update) => updates.push(update)),
    ).rejects.toThrow("OCR failed");

    expect(updates.map((update) => update.phase)).toEqual([
      "uploading",
      "indexing",
      "failed",
    ]);
    expect(updates[2]?.message).toBe("OCR failed");
  });
});

describe("splitImageAndOtherFiles", () => {
  it("separates image files from non-image files", () => {
    const png = new File(["img"], "photo.png", { type: "image/png" });
    const jpg = new File(["img"], "photo.jpg", { type: "image/jpeg" });
    const pdf = new File(["doc"], "report.pdf", { type: "application/pdf" });
    const txt = new File(["text"], "notes.txt", { type: "text/plain" });

    const { imageFiles, otherFiles } = splitImageAndOtherFiles([png, pdf, jpg, txt]);

    expect(imageFiles).toEqual([png, jpg]);
    expect(otherFiles).toEqual([pdf, txt]);
  });

  it("returns empty arrays for empty input", () => {
    const { imageFiles, otherFiles } = splitImageAndOtherFiles([]);
    expect(imageFiles).toEqual([]);
    expect(otherFiles).toEqual([]);
  });

  it("handles all-image input", () => {
    const gif = new File(["img"], "anim.gif", { type: "image/gif" });
    const webp = new File(["img"], "pic.webp", { type: "image/webp" });

    const { imageFiles, otherFiles } = splitImageAndOtherFiles([gif, webp]);

    expect(imageFiles).toEqual([gif, webp]);
    expect(otherFiles).toEqual([]);
  });

  it("handles all-non-image input", () => {
    const pdf = new File(["doc"], "report.pdf", { type: "application/pdf" });

    const { imageFiles, otherFiles } = splitImageAndOtherFiles([pdf]);

    expect(imageFiles).toEqual([]);
    expect(otherFiles).toEqual([pdf]);
  });

  it("does not treat unsupported image types as images", () => {
    const svg = new File(["svg"], "icon.svg", { type: "image/svg+xml" });
    const bmp = new File(["bmp"], "pic.bmp", { type: "image/bmp" });

    const { imageFiles, otherFiles } = splitImageAndOtherFiles([svg, bmp]);

    expect(imageFiles).toEqual([]);
    expect(otherFiles).toEqual([svg, bmp]);
  });
});
