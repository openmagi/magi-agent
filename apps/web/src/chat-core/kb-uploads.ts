import { isImageMimetype } from "./attachment-marker";
import { createMarker } from "./attachment-marker";
import { uploadAttachment } from "./attachments";
import type { KbDocReference } from "./types";

// Inlined from @/lib/knowledge/upload-mime (chat-core boundary forbids @/ imports).
// Reproduces only the resolveKnowledgeUploadMimeType chain used below.
const SOURCE_TEXT_EXTENSIONS = [
  "py", "pyw", "rpy", "ipynb",
  "js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts",
  "c", "h", "cpp", "cc", "cxx", "hpp", "hh", "hxx",
  "java", "kt", "kts", "swift", "go", "rs", "rb", "php", "cs",
  "sh", "bash", "zsh", "fish", "ps1", "bat", "cmd",
  "sql", "r", "scala", "sc", "dart", "lua", "pl", "pm",
  "ex", "exs", "erl", "hrl", "fs", "fsx", "fsi",
  "clj", "cljs", "edn", "hs", "lhs", "elm",
  "vue", "svelte", "css", "scss", "sass", "less",
  "yaml", "yml", "toml", "ini", "cfg", "conf", "env", "properties",
  "gitignore", "dockerfile", "makefile", "mk", "cmake",
  "gradle", "proto", "graphql", "gql", "lock", "patch", "diff",
];

const SOURCE_TEXT_MIME_BY_EXTENSION = Object.fromEntries(
  SOURCE_TEXT_EXTENSIONS.map((ext) => [ext, "text/plain"]),
) as Record<string, string>;

const KB_MIME_BY_EXTENSION: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  xls: "application/vnd.ms-excel",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  hwp: "application/x-hwp",
  hwpx: "application/x-hwpx",
  html: "text/html",
  htm: "text/html",
  epub: "application/epub+zip",
  csv: "text/csv",
  txt: "text/plain",
  md: "text/markdown",
  json: "application/json",
  xml: "application/xml",
  xhtml: "application/xhtml+xml",
  zip: "application/zip",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  gif: "image/gif",
  webp: "image/webp",
  ...SOURCE_TEXT_MIME_BY_EXTENSION,
};

function getKnowledgeUploadExtension(name: string): string {
  return name.split(".").pop()?.toLowerCase() || "";
}

function mimeFromKnowledgeUploadExtension(name: string): string {
  return KB_MIME_BY_EXTENSION[getKnowledgeUploadExtension(name)] || "application/octet-stream";
}

function resolveKnowledgeUploadMimeType(file: { name: string; type?: string | null }): string {
  const extensionMimeType = mimeFromKnowledgeUploadExtension(file.name);
  if (extensionMimeType !== "application/octet-stream") return extensionMimeType;
  if (file.type && file.type !== "application/octet-stream") return file.type;
  return extensionMimeType;
}

export interface SplitFiles {
  imageFiles: File[];
  otherFiles: File[];
}

export function splitImageAndOtherFiles(files: File[]): SplitFiles {
  const imageFiles: File[] = [];
  const otherFiles: File[] = [];
  for (const file of files) {
    if (isImageMimetype(file.type)) {
      imageFiles.push(file);
    } else {
      otherFiles.push(file);
    }
  }
  return { imageFiles, otherFiles };
}

export async function uploadImagesAsAttachmentMarkers(
  botId: string,
  channelName: string,
  imageFiles: File[],
): Promise<string> {
  const markers: string[] = [];
  for (const file of imageFiles) {
    const attachment = await uploadAttachment(botId, channelName, file);
    markers.push(createMarker(attachment.id, file.name));
  }
  return markers.join("\n");
}

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
