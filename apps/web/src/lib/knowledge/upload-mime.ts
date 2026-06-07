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

export const KB_UPLOAD_EXTENSIONS = new Set(Object.keys(KB_MIME_BY_EXTENSION));
export const KB_UPLOAD_ACCEPT = Object.keys(KB_MIME_BY_EXTENSION)
  .map((ext) => `.${ext}`)
  .join(",");

export function getKnowledgeUploadExtension(name: string): string {
  return name.split(".").pop()?.toLowerCase() || "";
}

export function mimeFromKnowledgeUploadExtension(name: string): string {
  return KB_MIME_BY_EXTENSION[getKnowledgeUploadExtension(name)] || "application/octet-stream";
}

export function resolveKnowledgeUploadMimeType(file: { name: string; type?: string | null }): string {
  const extensionMimeType = mimeFromKnowledgeUploadExtension(file.name);
  if (extensionMimeType !== "application/octet-stream") return extensionMimeType;
  if (file.type && file.type !== "application/octet-stream") return file.type;
  return extensionMimeType;
}

export function prepareKnowledgeUploadFile(file: File): File {
  const resolvedMimeType = resolveKnowledgeUploadMimeType(file);
  if (file.type === resolvedMimeType) return file;
  return new File([file], file.name, {
    type: resolvedMimeType,
    lastModified: file.lastModified,
  });
}

export interface KnowledgeUploadFileProgress {
  loaded: number;
  total: number;
  percent: number;
}

export function uploadKnowledgeFileToSignedUrl(
  uploadUrl: string,
  file: File,
  onProgress?: (progress: KnowledgeUploadFileProgress) => void,
): Promise<void> {
  const uploadFile = prepareKnowledgeUploadFile(file);
  const contentType = uploadFile.type || resolveKnowledgeUploadMimeType(uploadFile);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener("progress", (event) => {
      const total = event.lengthComputable ? event.total : uploadFile.size;
      const loaded = event.lengthComputable ? event.loaded : Math.min(event.loaded, total);
      const percent = total > 0 ? Math.round((loaded / total) * 100) : 0;
      onProgress?.({ loaded, total, percent });
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`Storage upload failed (${xhr.status})`));
      }
    });

    xhr.addEventListener("error", () => reject(new Error("Storage upload failed")));
    xhr.addEventListener("abort", () => reject(new Error("Storage upload cancelled")));

    xhr.open("PUT", uploadUrl);
    xhr.setRequestHeader("Content-Type", contentType);
    xhr.send(uploadFile);
  });
}
