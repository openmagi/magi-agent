export type WorkspaceFilePreviewKind = "text" | "image" | "pdf" | "download";

export interface WorkspaceFileEntry {
  path: string;
  filename: string;
  size: number;
  modifiedAt: string | null;
  extension: string;
  previewKind: WorkspaceFilePreviewKind;
}

interface RawWorkspaceFileEntry {
  path?: string | null;
  name?: string | null;
  size?: number | null;
  sizeBytes?: number | null;
  modifiedAt?: string | null;
  mtimeMs?: number | null;
  previewKind?: WorkspaceFilePreviewKind;
}

interface WorkspaceFileUrlOptions {
  botId?: string;
  path: string;
  mode?: "content" | "inline" | "download";
  maxBytes?: number;
}

const TEXT_EXTENSIONS = new Set([
  ".cjs",
  ".conf",
  ".css",
  ".csv",
  ".env",
  ".gitignore",
  ".html",
  ".js",
  ".json",
  ".jsonl",
  ".jsx",
  ".log",
  ".md",
  ".mdx",
  ".mjs",
  ".prompt",
  ".py",
  ".sh",
  ".sql",
  ".svg",
  ".toml",
  ".ts",
  ".tsx",
  ".txt",
  ".xml",
  ".yaml",
  ".yml",
]);

const IMAGE_EXTENSIONS = new Set([
  ".avif",
  ".gif",
  ".jpeg",
  ".jpg",
  ".png",
  ".webp",
]);

const DEFAULT_FILE_READ_BYTES = 256 * 1024;

function normalizePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/^\/+/, "");
}

function basename(path: string): string {
  const parts = normalizePath(path).split("/").filter(Boolean);
  return parts.at(-1) || path || "file";
}

function extensionFor(path: string): string {
  const name = basename(path).toLowerCase();
  if (name === ".gitignore" || name === ".env") return name;
  const index = name.lastIndexOf(".");
  return index > 0 ? name.slice(index) : "";
}

export function getWorkspaceFilePreviewKind(path: string): WorkspaceFilePreviewKind {
  const extension = extensionFor(path);
  if (IMAGE_EXTENSIONS.has(extension)) return "image";
  if (extension === ".pdf") return "pdf";
  if (TEXT_EXTENSIONS.has(extension) || extension === "") return "text";
  return "download";
}

export function normalizeWorkspaceFileList(
  entries: RawWorkspaceFileEntry[],
): WorkspaceFileEntry[] {
  const seen = new Map<string, WorkspaceFileEntry>();

  for (const entry of entries) {
    const path = normalizePath(entry.path || entry.name || "");
    if (!path || path === ".") continue;

    const size =
      typeof entry.size === "number" && Number.isFinite(entry.size)
        ? entry.size
        : typeof entry.sizeBytes === "number" && Number.isFinite(entry.sizeBytes)
          ? entry.sizeBytes
          : 0;
    const modifiedAt =
      typeof entry.modifiedAt === "string"
        ? entry.modifiedAt
        : typeof entry.mtimeMs === "number" && Number.isFinite(entry.mtimeMs)
          ? new Date(entry.mtimeMs).toISOString()
          : null;
    const extension = extensionFor(path);

    seen.set(path, {
      path,
      filename: basename(path),
      size,
      modifiedAt,
      extension,
      previewKind: entry.previewKind ?? getWorkspaceFilePreviewKind(path),
    });
  }

  return Array.from(seen.values()).sort((a, b) => a.path.localeCompare(b.path));
}

export function formatWorkspaceFileSize(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const formatted = unitIndex === 0 ? String(Math.round(value)) : value.toFixed(value >= 10 ? 1 : 2);
  return `${formatted} ${units[unitIndex]}`;
}

export function buildWorkspaceFileContentUrl({
  path,
  mode = "content",
  maxBytes = DEFAULT_FILE_READ_BYTES,
}: WorkspaceFileUrlOptions): string {
  const endpoint =
    mode === "content" ? "/v1/app/workspace/file" : "/v1/app/workspace/download";
  const params = new URLSearchParams({ path });
  if (mode === "content") {
    params.set("maxBytes", String(maxBytes));
  }
  return `${endpoint}?${params.toString()}`;
}
