"use client";

import type { Attachment } from "./types";

const MAX_FILE_SIZE = 1024 * 1024 * 1024; // 1GB

const ALLOWED_MIMETYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/gif",
  "image/webp",
  "audio/mpeg",
  "audio/mp3",
  "audio/mp4",
  "audio/x-m4a",
  "audio/wav",
  "audio/wave",
  "audio/x-wav",
  "audio/ogg",
  "audio/webm",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/x-hwp",
  "application/hwp+zip",
  "application/x-hwpx",
  "text/plain",
  "text/csv",
  "text/tab-separated-values",
  "text/markdown",
  "text/html",
  "text/xml",
  "application/xml",
  "application/xhtml+xml",
  "application/zip",
  "application/x-zip-compressed",
  "application/gzip",
  "application/x-gzip",
  "application/x-tar",
  "application/x-gtar",
  "application/json",
  "text/javascript",
  "application/javascript",
  "application/typescript",
  "text/typescript",
  "text/css",
  "text/x-python",
  "text/x-c",
  "text/x-c++src",
  "text/x-java-source",
  "text/x-go",
  "text/x-rustsrc",
  "text/x-shellscript",
  "application/x-sh",
  "text/x-sql",
  "text/x-yaml",
  "application/x-yaml",
  "text/yaml",
  "application/toml",
]);

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

const SOURCE_TEXT_MIMETYPES = Object.fromEntries(
  SOURCE_TEXT_EXTENSIONS.map((ext) => [ext, "text/plain"]),
) as Record<string, string>;

const EXTENSION_MIMETYPES: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  xls: "application/vnd.ms-excel",
  hwp: "application/x-hwp",
  hwpx: "application/hwp+zip",
  mp3: "audio/mpeg",
  m4a: "audio/mp4",
  wav: "audio/wav",
  ogg: "audio/ogg",
  oga: "audio/ogg",
  webm: "audio/webm",
  md: "text/markdown",
  markdown: "text/markdown",
  txt: "text/plain",
  csv: "text/csv",
  tsv: "text/tab-separated-values",
  html: "text/html",
  htm: "text/html",
  xml: "application/xml",
  xhtml: "application/xhtml+xml",
  json: "application/json",
  zip: "application/zip",
  gz: "application/gzip",
  tgz: "application/gzip",
  tar: "application/x-tar",
  ...SOURCE_TEXT_MIMETYPES,
};

export const CHAT_ATTACHMENT_ACCEPT = [
  ...Array.from(ALLOWED_MIMETYPES),
  ...Object.keys(EXTENSION_MIMETYPES).map((ext) => `.${ext}`),
].join(",");

type TokenGetter = () => Promise<string | null>;
let _getToken: TokenGetter = async () => null;

export function setAttachmentTokenGetter(getter: TokenGetter): void {
  _getToken = getter;
}

async function getToken(): Promise<string> {
  for (let i = 0; i < 5; i++) {
    const token = await _getToken();
    if (token) return token;
    if (i < 4) await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("Auth expired");
}

export function validateFile(file: File): string | null {
  if (file.size > MAX_FILE_SIZE) return "File size exceeds 1GB limit";
  const effectiveMime = getEffectiveMimeType(file);
  if (!ALLOWED_MIMETYPES.has(effectiveMime)) return `File type ${effectiveMime} is not supported`;
  return null;
}

function getEffectiveMimeType(file: File): string {
  const ext = file.name.toLowerCase().split(".").pop();
  if (ext && EXTENSION_MIMETYPES[ext]) return EXTENSION_MIMETYPES[ext];
  if (ALLOWED_MIMETYPES.has(file.type)) return file.type;
  if (file.type && file.type !== "application/octet-stream") return file.type;
  return file.type;
}

/**
 * Upload a file attachment via direct upload to Supabase Storage.
 * Flow: request signed URL from chat-proxy → PUT file directly to Supabase.
 * Returns the created attachment record.
 */
export async function uploadAttachment(
  botId: string,
  channelName: string,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<Attachment> {
  void botId;
  void channelName;
  void file;
  void onProgress;
  throw new Error("Local OSS file upload uses Knowledge context; direct chat attachments are not available.");
}

/** PUT file directly to Supabase Storage signed URL with XHR for progress tracking. */
function directUpload(
  uploadUrl: string,
  file: File,
  mimetype: string,
  onProgress?: (percent: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`Direct upload failed: ${xhr.status}`));
      }
    });

    xhr.addEventListener("error", () => reject(new Error("Network error during upload")));
    xhr.addEventListener("abort", () => reject(new Error("Upload cancelled")));

    xhr.open("PUT", uploadUrl);
    xhr.setRequestHeader("Content-Type", mimetype);
    xhr.send(file);
  });
}

/** Get a download URL for an attachment (chat-proxy handles auth via redirect). */
export function getAttachmentUrl(botId: string, attachmentId: string): string {
  const params = new URLSearchParams({
    botId,
    path: attachmentId,
    mode: "download",
  });
  return `/v1/app/workspace/download?${params.toString()}`;
}

export function getKnowledgeDocumentUrl(botId: string, docId: string): string {
  const params = new URLSearchParams({ botId, path: docId });
  return `/v1/app/knowledge/file?${params.toString()}`;
}

/**
 * Authenticated fetch of an attachment as a Blob. Used by image previews
 * and artifact panel downloads — the direct `<img src={url}>` path is
 * unauthenticated and chat-proxy returns 401, so we proxy through fetch
 * with the Privy bearer token and hand back a blob the caller can wrap
 * in `URL.createObjectURL`.
 */
export async function fetchAttachmentBlob(url: string): Promise<Blob> {
  const token = await _getToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(url, { headers });
  if (!res.ok) throw new Error(`fetchAttachmentBlob: ${res.status}`);
  return res.blob();
}
