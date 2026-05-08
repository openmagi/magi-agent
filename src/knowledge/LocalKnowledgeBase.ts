import fs from "node:fs/promises";
import path from "node:path";

const SUPPORTED_EXTENSIONS = new Set([
  ".md",
  ".mdx",
  ".txt",
  ".json",
  ".csv",
  ".tsv",
  ".yaml",
  ".yml",
  ".html",
]);

const DEFAULT_MAX_READ_BYTES = 256 * 1024;
const MAX_SEARCH_FILE_BYTES = 96 * 1024;

export interface LocalKnowledgeDocument {
  collection: string;
  filename: string;
  title: string;
  path: string;
  objectKey: string;
  sizeBytes: number;
  mtimeMs: number;
}

export interface LocalKnowledgeCollection {
  name: string;
  path: string;
  documentCount: number;
  sizeBytes: number;
}

export interface LocalKnowledgeSearchResult extends LocalKnowledgeDocument {
  score: number;
  snippet: string;
}

export interface LocalKnowledgeReadResult {
  path: string;
  content: string;
  sizeBytes: number;
  mtimeMs: number;
  truncated: boolean;
}

export interface LocalKnowledgeRunResult {
  exitCode: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
  truncated: boolean;
}

export interface LocalKnowledgeCommandContext {
  workspaceRoot: string;
  spawnWorkspace?: { root: string };
}

function knowledgeRoot(workspaceRoot: string): string {
  return path.join(workspaceRoot, "knowledge");
}

function isSupportedKnowledgeFile(rel: string): boolean {
  return SUPPORTED_EXTENSIONS.has(path.posix.extname(rel).toLowerCase());
}

function ensureSafeRelative(raw: string): string | null {
  const value = raw.trim();
  if (!value || value.includes("\0") || path.isAbsolute(value)) return null;
  const normalized = path.posix.normalize(value.replace(/\\/g, "/"));
  if (
    normalized === "." ||
    normalized === ".." ||
    normalized.startsWith("../") ||
    normalized.startsWith("/")
  ) {
    return null;
  }
  return normalized.replace(/^\.\//, "");
}

export function normalizeKnowledgePath(raw: string): string | null {
  const safe = ensureSafeRelative(raw);
  if (!safe) return null;
  const rel = safe === "knowledge" || safe.startsWith("knowledge/")
    ? safe
    : path.posix.join("knowledge", safe);
  if (rel === "knowledge") return rel;
  if (!rel.startsWith("knowledge/")) return null;
  return rel;
}

function resolveKnowledgePath(
  workspaceRoot: string,
  raw: string,
): { rel: string; full: string } | null {
  const rel = normalizeKnowledgePath(raw);
  if (!rel) return null;
  const root = path.resolve(workspaceRoot);
  const full = path.resolve(root, rel);
  const knowledge = path.resolve(knowledgeRoot(workspaceRoot));
  if (full !== knowledge && !full.startsWith(`${knowledge}${path.sep}`)) return null;
  return { rel, full };
}

function collectionForRel(rel: string): string {
  const withoutRoot = rel.replace(/^knowledge\/?/, "");
  const first = withoutRoot.split("/").filter(Boolean)[0];
  return first && withoutRoot.includes("/") ? first : "default";
}

function titleFromContent(filename: string, content: string): string {
  const heading = content.match(/^#\s+(.+)$/m)?.[1]?.trim();
  if (heading) return heading.slice(0, 160);
  return filename.replace(/\.[^.]+$/, "");
}

function documentShape(
  rel: string,
  stat: { size: number; mtimeMs: number },
  title: string,
): LocalKnowledgeDocument {
  return {
    collection: collectionForRel(rel),
    filename: path.posix.basename(rel),
    title,
    path: rel,
    objectKey: rel,
    sizeBytes: stat.size,
    mtimeMs: stat.mtimeMs,
  };
}

async function readFilePrefix(fullPath: string, maxBytes: number): Promise<string> {
  const handle = await fs.open(fullPath, "r");
  try {
    const buffer = Buffer.alloc(maxBytes);
    const result = await handle.read(buffer, 0, maxBytes, 0);
    return buffer.subarray(0, result.bytesRead).toString("utf8");
  } finally {
    await handle.close();
  }
}

function toKbDocument(document: LocalKnowledgeDocument): Record<string, unknown> {
  return {
    id: document.objectKey,
    filename: document.filename,
    canonical_filename: document.filename,
    canonical_title: document.title,
    collection: document.collection,
    status: "ready",
    object_key_converted: document.objectKey,
    object_key_original: document.objectKey,
    converted_size: document.sizeBytes,
    chunk_count: 1,
    aliases: [document.title],
    search_hints: [document.collection, document.filename, document.title],
  };
}

function searchTerms(query: string): string[] {
  return query
    .normalize("NFC")
    .toLocaleLowerCase()
    .split(/[^\p{L}\p{N}_-]+/u)
    .map((term) => term.trim())
    .filter((term) => term.length >= 2);
}

function termOccurrences(haystack: string, term: string): number {
  let count = 0;
  let offset = 0;
  while (offset < haystack.length) {
    const found = haystack.indexOf(term, offset);
    if (found < 0) break;
    count += 1;
    offset = found + term.length;
  }
  return count;
}

function scoreKnowledgeDocument(
  document: LocalKnowledgeDocument,
  content: string,
  terms: string[],
): number {
  const title = document.title.toLocaleLowerCase();
  const filename = document.filename.toLocaleLowerCase();
  const body = content.toLocaleLowerCase();
  let score = 0;
  for (const term of terms) {
    if (title.includes(term)) score += 5;
    if (filename.includes(term)) score += 3;
    score += Math.min(8, termOccurrences(body, term));
  }
  return score;
}

function snippetFor(content: string, terms: string[], maxChars = 320): string {
  const lower = content.toLocaleLowerCase();
  const firstIndex = terms
    .map((term) => lower.indexOf(term))
    .filter((index) => index >= 0)
    .sort((a, b) => a - b)[0] ?? 0;
  const start = Math.max(0, firstIndex - 90);
  const snippet = content.slice(start, start + maxChars).replace(/\s+/g, " ").trim();
  return `${start > 0 ? "... " : ""}${snippet}${start + maxChars < content.length ? " ..." : ""}`;
}

export async function listLocalKnowledgeDocuments(
  workspaceRoot: string,
  collection?: string,
): Promise<LocalKnowledgeDocument[]> {
  const root = knowledgeRoot(workspaceRoot);
  const documents: LocalKnowledgeDocument[] = [];
  const collectionFilter = collection?.trim() || undefined;

  async function walk(fullDir: string, relDir: string): Promise<void> {
    let dirents: Array<import("node:fs").Dirent>;
    try {
      dirents = await fs.readdir(fullDir, { withFileTypes: true });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
      throw error;
    }

    for (const dirent of dirents) {
      const rel = path.posix.join(relDir, dirent.name);
      const full = path.join(fullDir, dirent.name);
      if (dirent.isDirectory()) {
        await walk(full, rel);
        continue;
      }
      if (!dirent.isFile() || !isSupportedKnowledgeFile(rel)) continue;
      const normalizedRel = path.posix.join("knowledge", rel);
      const docCollection = collectionForRel(normalizedRel);
      if (collectionFilter && docCollection !== collectionFilter) continue;
      const stat = await fs.stat(full);
      const prefix = await readFilePrefix(full, Math.min(stat.size, 16 * 1024));
      documents.push(documentShape(normalizedRel, stat, titleFromContent(dirent.name, prefix)));
    }
  }

  await walk(root, "");
  documents.sort((a, b) => a.path.localeCompare(b.path));
  return documents;
}

export async function listLocalKnowledgeCollections(
  workspaceRoot: string,
): Promise<LocalKnowledgeCollection[]> {
  const documents = await listLocalKnowledgeDocuments(workspaceRoot);
  const byName = new Map<string, LocalKnowledgeCollection>();
  for (const document of documents) {
    const current = byName.get(document.collection) ?? {
      name: document.collection,
      path:
        document.collection === "default"
          ? "knowledge"
          : path.posix.join("knowledge", document.collection),
      documentCount: 0,
      sizeBytes: 0,
    };
    current.documentCount += 1;
    current.sizeBytes += document.sizeBytes;
    byName.set(document.collection, current);
  }
  return Array.from(byName.values()).sort((a, b) => a.name.localeCompare(b.name));
}

export async function searchLocalKnowledge(
  workspaceRoot: string,
  query: string,
  opts: { collection?: string; limit?: number } = {},
): Promise<LocalKnowledgeSearchResult[]> {
  const terms = searchTerms(query);
  if (terms.length === 0) return [];
  const limit = Math.max(1, Math.min(50, Math.trunc(opts.limit ?? 10)));
  const documents = await listLocalKnowledgeDocuments(workspaceRoot, opts.collection);
  const results: LocalKnowledgeSearchResult[] = [];

  for (const document of documents) {
    const resolved = resolveKnowledgePath(workspaceRoot, document.path);
    if (!resolved) continue;
    const content = await readFilePrefix(
      resolved.full,
      Math.min(document.sizeBytes, MAX_SEARCH_FILE_BYTES),
    );
    const score = scoreKnowledgeDocument(document, content, terms);
    if (score <= 0) continue;
    results.push({
      ...document,
      score,
      snippet: snippetFor(content, terms),
    });
  }

  return results
    .sort((a, b) => b.score - a.score || a.path.localeCompare(b.path))
    .slice(0, limit);
}

export async function readLocalKnowledgeFile(
  workspaceRoot: string,
  rawPath: string,
  maxBytes = DEFAULT_MAX_READ_BYTES,
): Promise<LocalKnowledgeReadResult | null> {
  const resolved = resolveKnowledgePath(workspaceRoot, rawPath);
  if (!resolved || resolved.rel === "knowledge") return null;
  const stat = await fs.stat(resolved.full);
  if (!stat.isFile()) return null;
  const bytesToRead = Math.min(stat.size, Math.max(1, maxBytes));
  const content = await readFilePrefix(resolved.full, bytesToRead);
  return {
    path: resolved.rel,
    content,
    sizeBytes: stat.size,
    mtimeMs: stat.mtimeMs,
    truncated: stat.size > Buffer.byteLength(content, "utf8"),
  };
}

export async function writeLocalKnowledgeFile(
  workspaceRoot: string,
  rawPath: string,
  content: string,
): Promise<{ path: string; sizeBytes: number; mtimeMs: number } | null> {
  const resolved = resolveKnowledgePath(workspaceRoot, rawPath);
  if (!resolved || resolved.rel === "knowledge" || !isSupportedKnowledgeFile(resolved.rel)) {
    return null;
  }
  await fs.mkdir(path.dirname(resolved.full), { recursive: true });
  await fs.writeFile(resolved.full, content, "utf8");
  const stat = await fs.stat(resolved.full);
  return { path: resolved.rel, sizeBytes: stat.size, mtimeMs: stat.mtimeMs };
}

export async function runLocalKnowledgeCommand(
  args: string[],
  ctx: LocalKnowledgeCommandContext,
): Promise<LocalKnowledgeRunResult> {
  const workspaceRoot = ctx.spawnWorkspace?.root ?? ctx.workspaceRoot;
  try {
    if (args[0] === "--collections") {
      return okJson({ collections: await listLocalKnowledgeCollections(workspaceRoot) });
    }
    if (args[0] === "--documents" || args[0] === "--manifest") {
      const collection = args[1]?.trim() || undefined;
      const documents = (await listLocalKnowledgeDocuments(workspaceRoot, collection)).map(toKbDocument);
      return okJson({
        ...(collection ? { collection } : {}),
        documents,
      });
    }
    if (args[0] === "--guide") {
      const collection = args[1]?.trim() || "default";
      return okJson({
        collection,
        root: "knowledge",
        writePath:
          collection === "default"
            ? "knowledge/<document>.md"
            : `knowledge/${collection}/<document>.md`,
        search: "KnowledgeSearch({ mode: 'search', collection, query })",
        read: "KnowledgeSearch({ mode: 'get', objectKey })",
      });
    }
    if (args[0] === "--get") {
      const objectKey = args[1]?.trim() ?? "";
      const file = await readLocalKnowledgeFile(workspaceRoot, objectKey);
      if (!file) return error("knowledge object not found", 1);
      return {
        exitCode: 0,
        signal: null,
        stdout: file.content,
        stderr: "",
        truncated: file.truncated,
      };
    }

    const collection = args[0]?.trim() || undefined;
    const query = args[1]?.trim() ?? "";
    const limitRaw = Number.parseInt(args[2] ?? "10", 10);
    const limit = Number.isFinite(limitRaw) ? limitRaw : 10;
    const results = await searchLocalKnowledge(workspaceRoot, query, {
      collection,
      limit,
    });
    return okJson({
      query,
      collection: collection ?? null,
      results: results.map((result) => ({
        title: result.title,
        filename: result.filename,
        collection: result.collection,
        path: result.path,
        object_key_converted: result.objectKey,
        object_key_original: result.objectKey,
        score: result.score,
        snippet: result.snippet,
      })),
    });
  } catch (err) {
    return error(err instanceof Error ? err.message : String(err), 1);
  }
}

function okJson(value: unknown): LocalKnowledgeRunResult {
  return {
    exitCode: 0,
    signal: null,
    stdout: JSON.stringify(value),
    stderr: "",
    truncated: false,
  };
}

function error(message: string, exitCode: number): LocalKnowledgeRunResult {
  return {
    exitCode,
    signal: null,
    stdout: "",
    stderr: message,
    truncated: false,
  };
}
