import { resolveKnowledgeUploadMimeType } from "@/lib/knowledge/upload-mime";
import type { KbDocReference } from "./types";

export type PendingKbUploadPhase = "uploading" | "indexing" | "ready" | "failed";

export interface PendingKbUpload {
  key: string;
  filename: string;
  phase: PendingKbUploadPhase;
  message?: string;
  ref?: KbDocReference;
}

interface EnsureDefaultCollectionResponse {
  collection: {
    id: string;
    name: string;
  };
}

interface UploadUrlResponse {
  upload_url: string;
  storage_path: string;
}

interface UploadDocumentResponse {
  doc_id: string;
  collection_id: string;
  collection: string;
  filename: string;
  mime_type: string;
  status: "ready" | "error";
  error?: string;
}

export function kbUploadKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

export async function uploadChatFilesToKb(
  botId: string,
  files: File[],
  onUpdate: (update: PendingKbUpload) => void,
): Promise<KbDocReference[]> {
  const ensureResponse = await fetch("/api/knowledge/collections/default", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ botId }),
  });
  if (!ensureResponse.ok) {
    throw new Error("Failed to ensure Downloads collection");
  }

  const { collection } = await ensureResponse.json() as EnsureDefaultCollectionResponse;
  const refs: KbDocReference[] = [];

  for (const file of files) {
    const key = kbUploadKey(file);
    const mimeType = resolveKnowledgeUploadMimeType(file);

    try {
      onUpdate({
        key,
        filename: file.name,
        phase: "uploading",
        message: `Uploading ${file.name}...`,
      });

      const uploadUrlResponse = await fetch("/api/knowledge/upload-url", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          botId,
          collection: collection.name,
          filename: file.name,
          content_type: mimeType,
        }),
      });
      if (!uploadUrlResponse.ok) {
        throw new Error(`Failed to prepare upload for ${file.name}`);
      }

      const { upload_url, storage_path } = await uploadUrlResponse.json() as UploadUrlResponse;
      const putResponse = await fetch(upload_url, {
        method: "PUT",
        headers: {
          "Content-Type": mimeType,
          "x-upsert": "false",
        },
        body: file,
      });
      if (!putResponse.ok) {
        throw new Error(`Storage upload failed for ${file.name}`);
      }

      onUpdate({
        key,
        filename: file.name,
        phase: "indexing",
        message: `Indexing ${file.name}...`,
      });

      const uploadResponse = await fetch("/api/knowledge/upload", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          botId,
          collection: collection.name,
          filename: file.name,
          mime_type: mimeType,
          storage_path,
        }),
      });
      const uploadResult = await uploadResponse.json() as UploadDocumentResponse;
      if (!uploadResponse.ok) {
        throw new Error(uploadResult.error || `Failed to index ${file.name}`);
      }

      const ref: KbDocReference = {
        id: uploadResult.doc_id,
        filename: uploadResult.filename,
        collectionId: uploadResult.collection_id,
        collectionName: uploadResult.collection,
        mimeType: uploadResult.mime_type,
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
      onUpdate({
        key,
        filename: file.name,
        phase: "failed",
        message,
      });
      throw error;
    }
  }

  return refs;
}
