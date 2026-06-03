export type WorkspaceFilePreviewKind = "markdown" | "text" | "html" | "image" | "pdf" | "download";
export type WorkspaceFileContentMode = "content" | "inline" | "download";

export interface WorkspaceFileApiRow {
  path: string;
  size: number;
  modifiedAt?: string | null;
}

export interface WorkspaceFileEntry {
  path: string;
  filename: string;
  size: number;
  modifiedAt: string | null;
  previewKind: WorkspaceFilePreviewKind;
}

export interface WorkspaceFileTreeDirectory {
  type: "directory";
  name: string;
  path: string;
  fileCount: number;
  children: WorkspaceFileTreeNode[];
}

export interface WorkspaceFileTreeFile {
  type: "file";
  name: string;
  path: string;
  file: WorkspaceFileEntry;
}

export type WorkspaceFileTreeNode = WorkspaceFileTreeDirectory | WorkspaceFileTreeFile;

const MARKDOWN_EXTENSIONS = new Set(["md", "markdown"]);
const HTML_EXTENSIONS = new Set(["html", "htm"]);
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg"]);
const TEXT_EXTENSIONS = new Set([
  "txt", "csv", "tsv", "json", "yaml", "yml", "log", "xml",
  "py", "js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts",
  "css", "scss", "sass", "less", "html", "md", "sh", "zsh", "bash",
  "sql", "toml", "ini", "cfg", "conf", "env", "lock", "patch", "diff",
]);

function extensionFor(filePath: string): string {
  const name = filePath.split("/").pop() ?? filePath;
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

export function filenameForWorkspacePath(filePath: string): string {
  return filePath.split("/").filter(Boolean).pop() ?? filePath;
}

export function getWorkspaceFilePreviewKind(filePath: string): WorkspaceFilePreviewKind {
  const ext = extensionFor(filePath);
  if (MARKDOWN_EXTENSIONS.has(ext)) return "markdown";
  if (HTML_EXTENSIONS.has(ext)) return "html";
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (TEXT_EXTENSIONS.has(ext)) return "text";
  return "download";
}

export function buildWorkspaceFileContentUrl({
  botId,
  path,
  mode,
}: {
  botId: string;
  path: string;
  mode: WorkspaceFileContentMode;
}): string {
  const params = new URLSearchParams({ path, mode });
  return `/api/bots/${encodeURIComponent(botId)}/workspace-files?${params.toString()}`;
}

export function normalizeWorkspaceFileList(rows: WorkspaceFileApiRow[]): WorkspaceFileEntry[] {
  return rows.map((row) => ({
    path: row.path,
    filename: filenameForWorkspacePath(row.path),
    size: row.size,
    modifiedAt: row.modifiedAt ?? null,
    previewKind: getWorkspaceFilePreviewKind(row.path),
  }));
}

function createDirectory(name: string, path: string): WorkspaceFileTreeDirectory {
  return { type: "directory", name, path, fileCount: 0, children: [] };
}

export function buildWorkspaceFileTree(files: WorkspaceFileEntry[]): WorkspaceFileTreeNode[] {
  const root = createDirectory("", "");
  const directories = new Map<string, WorkspaceFileTreeDirectory>([["", root]]);

  for (const file of files) {
    const parts = file.path.split("/").filter(Boolean);
    const name = parts.pop() ?? file.filename;
    const ancestors = [root];
    let parent = root;
    let currentPath = "";

    for (const part of parts) {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      let directory = directories.get(currentPath);
      if (!directory) {
        directory = createDirectory(part, currentPath);
        directories.set(currentPath, directory);
        parent.children.push(directory);
      }
      ancestors.push(directory);
      parent = directory;
    }

    for (const directory of ancestors) {
      directory.fileCount += 1;
    }

    parent.children.push({ type: "file", name, path: file.path, file });
  }

  return root.children;
}

export function formatWorkspaceFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
