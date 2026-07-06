// Self-host chat attachment upload.
//
// The hosted upload path (`chat-core/kb-uploads.ts`) POSTs to `/api/knowledge/*`
// (Supabase Storage + hosted indexer), which does not exist in the packaged
// local runtime. This app-layer helper is the `botId === "local"` branch: it
// streams each file's raw bytes to the runtime's `POST /v1/app/knowledge/upload`
// endpoint, which writes them under `<workspace>/knowledge/Downloads/`. The
// returned `doc_id` is the workspace-relative path, which the local KB_CONTEXT
// resolver (magi_agent/transport/kb_context.py) reads back and inlines into the
// agent turn.
//
// This lives in `lib/chat` (app layer) rather than `chat-core` because it needs
// `agentFetch` from `@/lib/local-api`, and the chat-core boundary forbids `@/`
// imports.

import type { KbDocReference, PendingKbUpload } from "@/chat-core";
import { kbUploadKey } from "@/chat-core";
import { agentFetch } from "@/lib/local-api";

const LOCAL_KB_COLLECTION = "Downloads";

// Minimal extension → mime map for the Content-Type header. The runtime only
// uses this as a hint (extension-based dispatch drives real conversion), so a
// conservative table plus the browser-provided `file.type` fallback suffices.
const MIME_BY_EXTENSION: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  hwp: "application/x-hwp",
  hwpx: "application/x-hwpx",
  csv: "text/csv",
  txt: "text/plain",
  md: "text/markdown",
  json: "application/json",
  xml: "application/xml",
  html: "text/html",
  htm: "text/html",
  zip: "application/zip",
};

function resolveLocalUploadMime(file: File): string {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return MIME_BY_EXTENSION[ext] || file.type || "application/octet-stream";
}

interface LocalUploadResponse {
  doc_id: string;
  collection_id: string;
  collection: string;
  filename: string;
  mime_type: string;
  status: "ready" | "error";
  error?: string;
}

/**
 * Upload chat-attached documents into the local workspace KB. Emits the same
 * `PendingKbUpload` phase updates as the hosted `uploadChatFilesToKb` so the
 * composer's progress UI is identical.
 */
export async function uploadChatFilesToLocalKb(
  files: File[],
  onUpdate: (update: PendingKbUpload) => void,
): Promise<KbDocReference[]> {
  const refs: KbDocReference[] = [];

  for (const file of files) {
    const key = kbUploadKey(file);
    const mimeType = resolveLocalUploadMime(file);
    try {
      onUpdate({
        key,
        filename: file.name,
        phase: "uploading",
        message: `Uploading ${file.name}...`,
      });

      const res = await agentFetch("/v1/app/knowledge/upload", {
        method: "POST",
        headers: {
          "Content-Type": mimeType,
          "x-filename": encodeURIComponent(file.name),
          "x-collection": LOCAL_KB_COLLECTION,
        },
        body: file,
      });

      onUpdate({
        key,
        filename: file.name,
        phase: "indexing",
        message: `Indexing ${file.name}...`,
      });

      const result = (await res.json()) as LocalUploadResponse;
      if (!res.ok || result.status !== "ready") {
        throw new Error(result.error || `Failed to store ${file.name}`);
      }

      const ref: KbDocReference = {
        id: result.doc_id,
        filename: result.filename,
        collectionId: result.collection_id,
        collectionName: result.collection,
        mimeType: result.mime_type,
        source: "chat_upload",
      };
      refs.push(ref);

      onUpdate({
        key,
        filename: file.name,
        phase: "ready",
        message: `${file.name} ready`,
        ref,
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : `Failed to process ${file.name}`;
      onUpdate({ key, filename: file.name, phase: "failed", message });
      throw error;
    }
  }

  return refs;
}
