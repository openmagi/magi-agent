export type KnowledgeScope = "personal" | "org";
export type KnowledgeOrgRole = "admin" | "member" | null | undefined;
export type KnowledgeDocumentStatus = "ready" | "processing" | "pending" | "error" | string;
export type KnowledgeDocumentStatusFilter = "all" | "ready" | "processing" | "pending" | "error";
export type KnowledgeDocumentSourceFilter = "all" | "file" | "notion" | "zip" | "other";

export interface KnowledgeConsoleCollection {
  id: string;
  name: string;
  document_count: number;
  error_count?: number | null;
  total_chunks: number;
  created_at: string;
}

export interface KnowledgeConsoleDocument {
  id: string;
  filename: string;
  aliases?: string[] | null;
  original_size: number | null;
  converted_size?: number | null;
  object_key_original?: string | null;
  object_key_converted?: string | null;
  chunk_count: number | null;
  status: KnowledgeDocumentStatus;
  error_message?: string | null;
  created_at: string;
  parent_document_id?: string | null;
  path?: string | null;
  sort_order?: number | null;
  source_provider?: string | null;
  source_external_id?: string | null;
  source_parent_external_id?: string | null;
}

export interface PagedWindow<T> {
  rows: T[];
  total: number;
  totalPages: number;
  page: number;
  pageSize: number;
}

export interface KnowledgePermissions {
  canManageContent: boolean;
  canSyncNotion: boolean;
}

export function buildKnowledgePermissions({
  scope,
  orgRole,
}: {
  scope: KnowledgeScope;
  orgRole?: KnowledgeOrgRole;
}): KnowledgePermissions {
  if (scope === "personal") {
    return { canManageContent: true, canSyncNotion: true };
  }

  return {
    canManageContent: Boolean(orgRole),
    canSyncNotion: orgRole === "admin",
  };
}

export function summarizeCollectionStatus(collection: KnowledgeConsoleCollection): {
  readyCount: number;
  errorCount: number;
  chunkCount: number;
  label: string;
} {
  const errorCount = collection.error_count ?? 0;
  const readyCount = Math.max(0, collection.document_count - errorCount);
  const chunkCount = collection.total_chunks;

  return {
    readyCount,
    errorCount,
    chunkCount,
    label: `${readyCount} ready · ${errorCount} failed · ${chunkCount} chunks`,
  };
}

function normalizeSearch(value: string): string {
  return value.normalize("NFKC").trim().toLocaleLowerCase();
}

function addSearchValue(target: Set<string>, value: string | null | undefined): void {
  const raw = String(value ?? "").trim();
  if (!raw) return;
  for (const variant of [raw, raw.normalize("NFC"), raw.normalize("NFD"), raw.normalize("NFKC")]) {
    const normalized = normalizeSearch(variant);
    if (normalized) target.add(normalized);
  }
}

function extensionlessFilename(value: string | null | undefined): string {
  const filename = String(value ?? "").split(/[\\/]/).filter(Boolean).pop() ?? "";
  const lastDot = filename.lastIndexOf(".");
  if (lastDot <= 0) return filename;
  return filename.slice(0, lastDot);
}

function getDocumentSearchHaystack(document: KnowledgeConsoleDocument): string {
  const values = new Set<string>();
  addSearchValue(values, document.filename);
  addSearchValue(values, extensionlessFilename(document.filename));
  addSearchValue(values, document.path);
  addSearchValue(values, extensionlessFilename(document.path));
  addSearchValue(values, document.error_message);
  addSearchValue(values, document.source_provider);
  addSearchValue(values, document.source_external_id);
  addSearchValue(values, document.source_parent_external_id);
  for (const alias of document.aliases ?? []) {
    addSearchValue(values, alias);
  }
  return Array.from(values).join(" ");
}

export function paginate<T>({
  rows,
  page,
  pageSize,
}: {
  rows: T[];
  page: number;
  pageSize: number;
}): PagedWindow<T> {
  const safePageSize = Math.max(1, pageSize);
  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / safePageSize));
  const safePage = Math.min(Math.max(0, page), totalPages - 1);
  const start = safePage * safePageSize;

  return {
    rows: rows.slice(start, start + safePageSize),
    total,
    totalPages,
    page: safePage,
    pageSize: safePageSize,
  };
}

export function filterCollections(
  collections: KnowledgeConsoleCollection[],
  search: string,
): KnowledgeConsoleCollection[] {
  const query = normalizeSearch(search);
  if (!query) return collections;
  return collections.filter((collection) => collection.name.toLocaleLowerCase().includes(query));
}

export function getPagedCollectionWindow({
  collections,
  search,
  page,
  pageSize,
}: {
  collections: KnowledgeConsoleCollection[];
  search: string;
  page: number;
  pageSize: number;
}): PagedWindow<KnowledgeConsoleCollection> {
  return paginate({
    rows: filterCollections(collections, search),
    page,
    pageSize,
  });
}

function getDocumentSource(document: KnowledgeConsoleDocument): KnowledgeDocumentSourceFilter {
  const provider = document.source_provider?.toLocaleLowerCase();
  if (provider === "notion") return "notion";

  const filename = document.filename.toLocaleLowerCase();
  if (filename.endsWith(".zip")) return "zip";
  if (provider) return "other";
  return "file";
}

export function filterDocuments({
  documents,
  search,
  status,
  source,
}: {
  documents: KnowledgeConsoleDocument[];
  search: string;
  status: KnowledgeDocumentStatusFilter;
  source: KnowledgeDocumentSourceFilter;
}): KnowledgeConsoleDocument[] {
  const query = normalizeSearch(search);

  return documents.filter((document) => {
    if (query) {
      const haystack = getDocumentSearchHaystack(document);
      if (!haystack.includes(query)) return false;
    }

    if (status !== "all" && document.status !== status) return false;
    if (source !== "all" && getDocumentSource(document) !== source) return false;
    return true;
  });
}

export function getPagedDocumentWindow({
  documents,
  search,
  status,
  source,
  page,
  pageSize,
}: {
  documents: KnowledgeConsoleDocument[];
  search: string;
  status: KnowledgeDocumentStatusFilter;
  source: KnowledgeDocumentSourceFilter;
  page: number;
  pageSize: number;
}): PagedWindow<KnowledgeConsoleDocument> {
  return paginate({
    rows: filterDocuments({ documents, search, status, source }),
    page,
    pageSize,
  });
}
