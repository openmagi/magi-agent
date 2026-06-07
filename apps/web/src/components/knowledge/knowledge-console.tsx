"use client";

import { useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { flattenKnowledgeDocumentTree } from "@/lib/knowledge/document-tree";
import type { KnowledgeUploadProgressUpdate } from "@/lib/knowledge/console-client-actions";
import {
  getPagedCollectionWindow,
  getPagedDocumentWindow,
  summarizeCollectionStatus,
  type KnowledgeConsoleCollection,
  type KnowledgeConsoleDocument,
  type KnowledgeDocumentSourceFilter,
  type KnowledgeDocumentStatusFilter,
  type KnowledgePermissions,
  type KnowledgeScope,
} from "@/lib/knowledge/console-model";
import { KB_UPLOAD_ACCEPT } from "@/lib/knowledge/upload-mime";

const COLLECTION_PAGE_SIZE = 40;
const DOCUMENT_PAGE_SIZE = 50;

interface NotionPage {
  id: string;
  title: string;
}

interface PreviewState {
  docId: string;
  filename: string;
  content: string;
  mode: "markdown" | "csv";
  loading: boolean;
  error: string | null;
}

export interface KnowledgeConsoleProps {
  title: string;
  description: string;
  scope: KnowledgeScope;
  collections: KnowledgeConsoleCollection[];
  documents: KnowledgeConsoleDocument[];
  selectedCollectionName: string | null;
  permissions: KnowledgePermissions;
  loadingCollections: boolean;
  loadingDocuments: boolean;
  quota: { usedBytes: number; quotaBytes: number } | null;
  onSelectCollection: (collection: KnowledgeConsoleCollection) => void;
  onCreateCollection: (name: string) => Promise<void>;
  onUploadFiles: (
    collectionName: string,
    files: File[],
    onProgress?: (update: KnowledgeUploadProgressUpdate) => void,
  ) => Promise<{ uploaded: number; failures: Array<{ filename: string; reason: string }> }>;
  onDeleteDocuments: (
    documents: KnowledgeConsoleDocument[],
  ) => Promise<{ deleted: number; failures: Array<{ filename: string; reason: string }> }>;
  onDeleteCollection: (collection: KnowledgeConsoleCollection) => Promise<void>;
  onOpenNotion: () => Promise<{ workspace: string; pages: NotionPage[] }>;
  onSyncNotion: (collectionName: string, pageIds: string[]) => Promise<{ synced: number; errors: string[] }>;
  onFetchPreview: (document: KnowledgeConsoleDocument) => Promise<{ filename: string; content: string; mode: "markdown" | "csv" }>;
  onDownloadDocument: (document: KnowledgeConsoleDocument, type: "original" | "converted") => Promise<void>;
}

function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function statusLabel(status: string): string {
  if (status === "ready") return "Indexed";
  if (status === "processing") return "Indexing";
  if (status === "error") return "Failed";
  return status;
}

function parsePreviewTable(text: string): string[][] {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .slice(0, 80)
    .map((line) => line.split(/\t|,/).slice(0, 12));
}

export function KnowledgeConsole({
  title,
  description,
  scope,
  collections,
  documents,
  selectedCollectionName,
  permissions,
  loadingCollections,
  loadingDocuments,
  quota,
  onSelectCollection,
  onCreateCollection,
  onUploadFiles,
  onDeleteDocuments,
  onDeleteCollection,
  onOpenNotion,
  onSyncNotion,
  onFetchPreview,
  onDownloadDocument,
}: KnowledgeConsoleProps): React.ReactElement {
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const [collectionSearch, setCollectionSearch] = useState("");
  const [collectionPage, setCollectionPage] = useState(0);
  const [documentSearch, setDocumentSearch] = useState("");
  const [documentPage, setDocumentPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState<KnowledgeDocumentStatusFilter>("all");
  const [sourceFilter, setSourceFilter] = useState<KnowledgeDocumentSourceFilter>("all");
  const [selectedDocIds, setSelectedDocIds] = useState<Set<string>>(new Set());
  const [selectedCollectionIds, setSelectedCollectionIds] = useState<Set<string>>(new Set());
  const [collapsedDocIds, setCollapsedDocIds] = useState<Set<string>>(new Set());
  const [showCreate, setShowCreate] = useState(false);
  const [newCollectionName, setNewCollectionName] = useState("");
  const [creating, setCreating] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadItems, setUploadItems] = useState<KnowledgeUploadProgressUpdate[]>([]);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [notionOpen, setNotionOpen] = useState(false);
  const [notionWorkspace, setNotionWorkspace] = useState("Notion");
  const [notionPages, setNotionPages] = useState<NotionPage[]>([]);
  const [selectedNotionPages, setSelectedNotionPages] = useState<Set<string>>(new Set());
  const [syncingNotion, setSyncingNotion] = useState(false);

  const collectionWindow = useMemo(
    () =>
      getPagedCollectionWindow({
        collections,
        search: collectionSearch,
        page: collectionPage,
        pageSize: COLLECTION_PAGE_SIZE,
      }),
    [collectionPage, collectionSearch, collections],
  );

  const flattenedDocuments = useMemo(
    () => flattenKnowledgeDocumentTree(documents, collapsedDocIds),
    [collapsedDocIds, documents],
  );
  const rowMeta = useMemo(() => {
    const map = new Map<string, { depth: number; hasChildren: boolean }>();
    for (const row of flattenedDocuments) {
      map.set(row.doc.id, { depth: row.depth, hasChildren: row.hasChildren });
    }
    return map;
  }, [flattenedDocuments]);
  const flattenedDocs = useMemo(
    () => flattenedDocuments.map((row) => row.doc),
    [flattenedDocuments],
  );
  const documentWindow = useMemo(
    () =>
      getPagedDocumentWindow({
        documents: flattenedDocs,
        search: documentSearch,
        status: statusFilter,
        source: sourceFilter,
        page: documentPage,
        pageSize: DOCUMENT_PAGE_SIZE,
      }),
    [documentPage, documentSearch, flattenedDocs, sourceFilter, statusFilter],
  );

  const selectedDocuments = flattenedDocs.filter((document) => selectedDocIds.has(document.id));

  const createCollection = async () => {
    const name = newCollectionName.trim();
    if (!name) return;
    setCreating(true);
    setActionError(null);
    try {
      await onCreateCollection(name);
      setNewCollectionName("");
      setShowCreate(false);
      setActionMessage(`Collection "${name}" created`);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Collection creation failed");
    } finally {
      setCreating(false);
    }
  };

  const deleteCollection = async (collection: KnowledgeConsoleCollection) => {
    if (typeof window !== "undefined" && !window.confirm(`Delete collection "${collection.name}" and all its documents?`)) return;
    setActionError(null);
    try {
      await onDeleteCollection(collection);
      setActionMessage(`Collection "${collection.name}" deleted`);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Failed to delete collection");
    }
  };

  const handleFiles = async (files: File[]) => {
    if (!selectedCollectionName || files.length === 0) return;
    setUploading(true);
    setActionError(null);
    setActionMessage(`Uploading ${files.length} file(s)...`);
    setUploadItems(files.map((file, index) => ({
      index,
      filename: file.name,
      phase: "queued",
      progress: 0,
    })));
    try {
      const result = await onUploadFiles(selectedCollectionName, files, (update) => {
        setUploadItems((current) => {
          const next = current.slice();
          next[update.index] = { ...(next[update.index] ?? update), ...update };
          return next;
        });
      });
      const parts = [`${result.uploaded} uploaded`];
      if (result.failures.length > 0) parts.push(`${result.failures.length} failed`);
      setActionMessage(parts.join(" · "));
      setActionError(
        result.failures.length > 0
          ? result.failures.slice(0, 3).map((failure) => `${failure.filename}: ${failure.reason}`).join(" · ")
          : null,
      );
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const uploadPhaseLabel = (item: KnowledgeUploadProgressUpdate): string => {
    if (item.message) return item.message;
    if (item.phase === "queued") return "Queued";
    if (item.phase === "uploading") return "Uploading";
    if (item.phase === "indexing") return "Indexing";
    if (item.phase === "ready") return "Indexed";
    return "Failed";
  };

  const deleteDocuments = async (targets: KnowledgeConsoleDocument[]) => {
    if (targets.length === 0) return;
    if (typeof window !== "undefined" && !window.confirm(`Delete ${targets.length} document(s)?`)) return;
    setActionError(null);
    try {
      const result = await onDeleteDocuments(targets);
      setActionMessage(`${result.deleted} document(s) deleted`);
      setActionError(
        result.failures.length > 0
          ? result.failures.slice(0, 3).map((failure) => `${failure.filename}: ${failure.reason}`).join(" · ")
          : null,
      );
      setSelectedDocIds(new Set());
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Delete failed");
    }
  };

  const fetchPreview = async (document: KnowledgeConsoleDocument) => {
    setPreview({ docId: document.id, filename: document.filename, content: "", mode: "markdown", loading: true, error: null });
    try {
      const result = await onFetchPreview(document);
      setPreview({ docId: document.id, filename: result.filename, content: result.content, mode: result.mode, loading: false, error: null });
    } catch (error) {
      setPreview({
        docId: document.id,
        filename: document.filename,
        content: "",
        mode: "markdown",
        loading: false,
        error: error instanceof Error ? error.message : "Preview failed",
      });
    }
  };

  const downloadDocument = async (document: KnowledgeConsoleDocument, type: "original" | "converted") => {
    setActionError(null);
    try {
      await onDownloadDocument(document, type);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Download failed");
    }
  };

  const renderPreviewContent = () => {
    if (!preview) return null;

    if (preview.loading) {
      return <p className="text-sm text-secondary">Loading preview...</p>;
    }

    if (preview.error) {
      return <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">{preview.error}</p>;
    }

    if (preview.mode === "csv") {
      return (
        <div className="overflow-auto rounded-lg border border-black/10">
          <table className="w-full min-w-[720px] text-left text-xs">
            <tbody>
              {parsePreviewTable(preview.content).map((row, rowIndex) => (
                <tr key={rowIndex} className="border-b border-black/[0.06]">
                  {row.map((cell, cellIndex) => (
                    <td key={`${rowIndex}-${cellIndex}`} className="max-w-[220px] truncate px-3 py-2">{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    return (
      <div className="prose-chat max-w-none text-sm">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {preview.content}
        </ReactMarkdown>
      </div>
    );
  };

  const openNotion = async () => {
    if (!permissions.canSyncNotion) return;
    setActionError(null);
    try {
      const result = await onOpenNotion();
      setNotionWorkspace(result.workspace);
      setNotionPages(result.pages);
      setSelectedNotionPages(new Set());
      setNotionOpen(true);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Failed to load Notion pages");
    }
  };

  const syncNotion = async () => {
    if (!selectedCollectionName || selectedNotionPages.size === 0) return;
    setSyncingNotion(true);
    setActionError(null);
    try {
      const result = await onSyncNotion(selectedCollectionName, Array.from(selectedNotionPages));
      setActionMessage(`Synced ${result.synced} Notion page(s)`);
      setActionError(result.errors.length > 0 ? result.errors.slice(0, 3).join(" · ") : null);
      setNotionOpen(false);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Notion sync failed");
    } finally {
      setSyncingNotion(false);
    }
  };

  return (
    <section className="flex min-h-[calc(100vh-96px)] flex-col gap-4 text-foreground">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-bold tracking-normal">{title}</h1>
            <span className="rounded-md border border-black/10 px-2 py-1 text-xs font-medium text-secondary">
              {scope === "org" ? "Shared" : "Personal"}
            </span>
          </div>
          <p className="mt-1 max-w-2xl text-sm text-secondary">{description}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {quota && quota.quotaBytes > 0 ? (
            <div className="mr-2 min-w-[160px] text-right">
              <p className="text-xs text-secondary">{formatSize(quota.usedBytes)} / {formatSize(quota.quotaBytes)}</p>
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-black/10">
                <div
                  className={`h-full rounded-full ${quota.usedBytes / quota.quotaBytes > 0.9 ? "bg-red-500" : "bg-primary"}`}
                  style={{ width: `${Math.min(100, (quota.usedBytes / quota.quotaBytes) * 100)}%` }}
                />
              </div>
            </div>
          ) : null}
          {permissions.canManageContent ? (
            <>
              <button
                type="button"
                onClick={() => setShowCreate((open) => !open)}
                className="rounded-lg border border-black/10 bg-white px-3 py-2 text-sm font-semibold text-foreground hover:bg-black/[0.03]"
              >
                New Collection
              </button>
              <button
                type="button"
                onClick={() => uploadInputRef.current?.click()}
                disabled={!selectedCollectionName || uploading}
                className="rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white hover:bg-primary-light disabled:opacity-40"
              >
                {uploading ? "Uploading..." : "Upload"}
              </button>
              <button
                type="button"
                onClick={() => folderInputRef.current?.click()}
                disabled={!selectedCollectionName || uploading}
                className="rounded-lg border border-black/10 bg-white px-3 py-2 text-sm font-semibold text-foreground hover:bg-black/[0.03] disabled:opacity-40"
              >
                Folder
              </button>
            </>
          ) : null}
          {permissions.canSyncNotion ? (
            <button
              type="button"
              onClick={openNotion}
              disabled={!selectedCollectionName}
              className="rounded-lg border border-black/10 bg-white px-3 py-2 text-sm font-semibold text-foreground hover:bg-black/[0.03] disabled:opacity-40"
            >
              Sync Notion
            </button>
          ) : (
            <span className="rounded-lg border border-black/10 px-3 py-2 text-sm text-secondary">
              Notion sync requires admin
            </span>
          )}
          <input
            ref={uploadInputRef}
            type="file"
            multiple
            accept={KB_UPLOAD_ACCEPT}
            className="hidden"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              event.target.value = "";
              void handleFiles(files);
            }}
          />
          <input
            ref={folderInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              event.target.value = "";
              void handleFiles(files);
            }}
            {...{ webkitdirectory: "", directory: "" } as React.InputHTMLAttributes<HTMLInputElement>}
          />
        </div>
      </header>

      {showCreate ? (
        <div className="flex flex-col gap-2 rounded-lg border border-black/10 bg-white p-3 sm:flex-row">
          <input
            value={newCollectionName}
            onChange={(event) => setNewCollectionName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void createCollection();
            }}
            placeholder="Collection name"
            className="min-h-[42px] flex-1 rounded-lg border border-black/10 bg-white px-3 text-sm outline-none focus:border-primary/50"
          />
          <button
            type="button"
            onClick={() => void createCollection()}
            disabled={creating || !newCollectionName.trim()}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-light disabled:opacity-40"
          >
            {creating ? "Creating..." : "Create"}
          </button>
        </div>
      ) : null}

      {(actionMessage || actionError) ? (
        <div className="space-y-1">
          {actionMessage ? <p className="rounded-lg bg-black/[0.03] px-3 py-2 text-sm text-secondary">{actionMessage}</p> : null}
          {actionError ? <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">{actionError}</p> : null}
        </div>
      ) : null}

      {uploadItems.length > 0 ? (
        <div className="space-y-2 rounded-lg border border-black/10 bg-white p-3">
          {uploadItems.map((item) => (
            <div key={`${item.index}-${item.filename}`} className="grid gap-1.5">
              <div className="flex min-w-0 items-center justify-between gap-3 text-xs">
                <span className="truncate font-medium text-foreground">{item.filename}</span>
                <span className={item.phase === "failed" ? "shrink-0 text-red-600" : "shrink-0 text-secondary"}>
                  {uploadPhaseLabel(item)}
                </span>
              </div>
              <div
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(item.progress)}
                aria-label={`${item.filename} upload progress`}
                className="h-2 overflow-hidden rounded-full bg-black/10"
              >
                <div
                  className={`h-full rounded-full transition-all ${item.phase === "failed" ? "bg-red-500" : "bg-primary"}`}
                  style={{ width: `${Math.max(0, Math.min(100, item.progress))}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="flex min-h-[360px] flex-col overflow-hidden rounded-lg border border-black/10 bg-white">
          <div className="border-b border-black/10 p-3">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold">Collections</h2>
              <span className="text-xs text-secondary">{collections.length} collections</span>
            </div>
            <input
              value={collectionSearch}
              onChange={(event) => {
                setCollectionSearch(event.target.value);
                setCollectionPage(0);
              }}
              placeholder={`Search ${collections.length} collections...`}
              className="min-h-[38px] w-full rounded-lg border border-black/10 bg-white px-3 text-sm outline-none focus:border-primary/50"
            />
            {selectedCollectionIds.size > 0 && permissions.canManageContent ? (
              <button
                type="button"
                onClick={async () => {
                  const targets = collections.filter((c) => selectedCollectionIds.has(c.id));
                  if (targets.length === 0) return;
                  if (!window.confirm(`Delete ${targets.length} collection(s)? All documents inside will be permanently removed.`)) return;
                  for (const target of targets) {
                    await onDeleteCollection(target);
                  }
                  setSelectedCollectionIds(new Set());
                }}
                className="mt-2 w-full rounded-lg bg-red-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-600"
              >
                Delete {selectedCollectionIds.size} collection{selectedCollectionIds.size > 1 ? "s" : ""}
              </button>
            ) : null}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {loadingCollections ? (
              <p className="p-3 text-sm text-secondary">Loading collections...</p>
            ) : collectionWindow.rows.length === 0 ? (
              <p className="p-3 text-sm text-secondary">No collections found.</p>
            ) : (
              <div className="space-y-1">
                {collectionWindow.rows.map((collection) => {
                  const summary = summarizeCollectionStatus(collection);
                  const selected = selectedCollectionName === collection.name;
                  const isCollectionSelected = selectedCollectionIds.has(collection.id);
                  return (
                    <div key={collection.id} className="group flex items-center gap-1.5">
                      {permissions.canManageContent ? (
                        <input
                          type="checkbox"
                          checked={isCollectionSelected}
                          onChange={() => {
                            setSelectedCollectionIds((prev) => {
                              const next = new Set(prev);
                              if (next.has(collection.id)) next.delete(collection.id);
                              else next.add(collection.id);
                              return next;
                            });
                          }}
                          className="ml-1 h-3.5 w-3.5 shrink-0 rounded accent-primary"
                        />
                      ) : null}
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedDocIds(new Set());
                          setPreview(null);
                          onSelectCollection(collection);
                        }}
                        className={`min-w-0 flex-1 rounded-lg border px-3 py-2 text-left transition-colors ${
                          selected
                            ? "border-primary/50 bg-primary/5"
                            : "border-transparent hover:border-black/10 hover:bg-black/[0.03]"
                        }`}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm font-semibold">{collection.name}</span>
                          <span className="shrink-0 text-xs text-secondary">{collection.document_count}</span>
                        </span>
                        <span className="mt-0.5 block truncate text-xs text-secondary">{summary.label}</span>
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
          <div className="flex items-center justify-between border-t border-black/10 px-3 py-2 text-xs text-secondary">
            <span>Collections {collectionWindow.total === 0 ? 0 : collectionWindow.page * collectionWindow.pageSize + 1}-{Math.min(collectionWindow.total, (collectionWindow.page + 1) * collectionWindow.pageSize)} of {collectionWindow.total}</span>
            <span className="flex items-center gap-2">
              <button type="button" disabled={collectionWindow.page === 0} onClick={() => setCollectionPage((page) => Math.max(0, page - 1))} className="disabled:opacity-30">Prev</button>
              <button type="button" disabled={collectionWindow.page + 1 >= collectionWindow.totalPages} onClick={() => setCollectionPage((page) => page + 1)} className="disabled:opacity-30">Next</button>
            </span>
          </div>
        </aside>

        <main className="flex min-h-[480px] flex-col overflow-hidden rounded-lg border border-black/10 bg-white">
          <div className="border-b border-black/10 p-3">
            <div className="mb-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <h2 className="truncate text-sm font-semibold">{selectedCollectionName ?? "Select a collection"}</h2>
                <p className="text-xs text-secondary">{documents.length} documents</p>
              </div>
              {selectedDocuments.length > 0 ? (
                <button
                  type="button"
                  onClick={() => void deleteDocuments(selectedDocuments)}
                  className="rounded-lg bg-red-500 px-3 py-2 text-sm font-semibold text-white hover:bg-red-600"
                >
                  Delete {selectedDocuments.length}
                </button>
              ) : null}
            </div>
            <div className="grid gap-2 sm:grid-cols-[1fr_132px_120px]">
              <input
                value={documentSearch}
                onChange={(event) => {
                  setDocumentSearch(event.target.value);
                  setDocumentPage(0);
                }}
                placeholder={selectedCollectionName ? `Search ${documents.length} files...` : "Select a collection first"}
                className="min-h-[38px] rounded-lg border border-black/10 bg-white px-3 text-sm outline-none focus:border-primary/50"
              />
              <select
                value={statusFilter}
                onChange={(event) => {
                  setStatusFilter(event.target.value as KnowledgeDocumentStatusFilter);
                  setDocumentPage(0);
                }}
                className="min-h-[38px] rounded-lg border border-black/10 bg-white px-3 text-sm"
              >
                <option value="all">Any status</option>
                <option value="ready">Indexed</option>
                <option value="processing">Indexing</option>
                <option value="error">Failed</option>
                <option value="pending">Pending</option>
              </select>
              <select
                value={sourceFilter}
                onChange={(event) => {
                  setSourceFilter(event.target.value as KnowledgeDocumentSourceFilter);
                  setDocumentPage(0);
                }}
                className="min-h-[38px] rounded-lg border border-black/10 bg-white px-3 text-sm"
              >
                <option value="all">Any source</option>
                <option value="file">Files</option>
                <option value="notion">Notion</option>
                <option value="zip">Zip</option>
                <option value="other">Other</option>
              </select>
            </div>
          </div>
          <div className="grid grid-cols-[36px_minmax(220px,1fr)_88px_76px_132px_220px] border-b border-black/10 px-3 py-2 text-xs font-semibold text-secondary">
            <input
              type="checkbox"
              checked={documentWindow.rows.length > 0 && documentWindow.rows.every((d) => selectedDocIds.has(d.id))}
              onChange={() => {
                setSelectedDocIds((previous) => {
                  const visibleIds = documentWindow.rows.map((d) => d.id);
                  const allSelected = visibleIds.every((id) => previous.has(id));
                  const next = new Set(previous);
                  if (allSelected) {
                    for (const id of visibleIds) next.delete(id);
                  } else {
                    for (const id of visibleIds) next.add(id);
                  }
                  return next;
                });
              }}
              disabled={documentWindow.rows.length === 0}
              className="h-4 w-4 rounded accent-primary"
              aria-label="Select all visible documents"
            />
            <span>Name</span>
            <span>Status</span>
            <span>Size</span>
            <span>Preview</span>
            <span className="text-right">Download</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {loadingDocuments ? (
              <p className="p-4 text-sm text-secondary">Loading documents...</p>
            ) : !selectedCollectionName ? (
              <p className="p-4 text-sm text-secondary">Choose a collection to view documents.</p>
            ) : documentWindow.rows.length === 0 ? (
              <p className="p-4 text-sm text-secondary">No documents match this view.</p>
            ) : (
              documentWindow.rows.map((document) => {
                const meta = rowMeta.get(document.id) ?? { depth: 0, hasChildren: false };
                const isSelected = selectedDocIds.has(document.id);
                const isCollapsed = collapsedDocIds.has(document.id);
                const sourceLabel = document.source_provider === "notion"
                  ? "Notion"
                  : document.filename.endsWith(".zip") ? "Zip" : "File";
                return (
                  <div
                    key={document.id}
                    className="grid grid-cols-[36px_minmax(220px,1fr)_88px_76px_132px_220px] items-center border-b border-black/[0.06] px-3 py-2 text-sm hover:bg-black/[0.02]"
                  >
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => {
                        setSelectedDocIds((previous) => {
                          const next = new Set(previous);
                          if (next.has(document.id)) next.delete(document.id);
                          else next.add(document.id);
                          return next;
                        });
                      }}
                      className="h-4 w-4 rounded accent-primary"
                    />
                    <div className="min-w-0" style={{ paddingLeft: meta.depth * 18 }}>
                      <div className="flex min-w-0 items-center gap-1.5">
                        {meta.hasChildren ? (
                          <button
                            type="button"
                            onClick={() => {
                              setCollapsedDocIds((previous) => {
                                const next = new Set(previous);
                                if (next.has(document.id)) next.delete(document.id);
                                else next.add(document.id);
                                return next;
                              });
                            }}
                            className="flex h-5 w-5 shrink-0 items-center justify-center rounded hover:bg-black/[0.05]"
                            aria-label={isCollapsed ? "Expand document children" : "Collapse document children"}
                          >
                            <span className={`text-xs transition-transform ${isCollapsed ? "" : "rotate-90"}`}>›</span>
                          </button>
                        ) : (
                          <span className="h-5 w-5 shrink-0" />
                        )}
                        <button
                          type="button"
                          onClick={() => void fetchPreview(document)}
                          disabled={document.status !== "ready"}
                          className="min-w-0 truncate text-left font-medium hover:text-primary disabled:cursor-not-allowed disabled:text-secondary"
                        >
                          {document.filename}
                        </button>
                      </div>
                      <p className="truncate pl-6 text-xs text-secondary">
                        {sourceLabel} · {document.chunk_count ?? 0} chunks · {new Date(document.created_at).toLocaleDateString()}
                      </p>
                      {document.error_message ? <p className="truncate pl-6 text-xs text-red-500">{document.error_message}</p> : null}
                    </div>
                    <span className={`truncate text-xs ${document.status === "error" ? "text-red-500" : document.status === "ready" ? "text-green-600" : "text-amber-600"}`}>
                      {statusLabel(document.status)}
                    </span>
                    <span className="truncate text-xs text-secondary">{formatSize(document.original_size)}</span>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => void fetchPreview(document)}
                        disabled={document.status !== "ready"}
                        aria-label={`Preview markdown ${document.filename}`}
                        className="rounded-md border border-black/10 bg-white px-2 py-1 text-xs font-semibold hover:bg-black/[0.03] disabled:opacity-40"
                      >
                        Markdown
                      </button>
                    </div>
                    <div className="flex flex-wrap justify-end gap-1.5">
                      <button
                        type="button"
                        onClick={() => void downloadDocument(document, "original")}
                        disabled={document.status !== "ready" || !document.object_key_original}
                        aria-label={`Download original ${document.filename}`}
                        className="rounded-md border border-black/10 bg-white px-2 py-1 text-xs font-semibold hover:bg-black/[0.03] disabled:opacity-40"
                      >
                        Original
                      </button>
                      <button
                        type="button"
                        onClick={() => void downloadDocument(document, "converted")}
                        disabled={document.status !== "ready" || !document.object_key_converted}
                        aria-label={`Download markdown ${document.filename}`}
                        className="rounded-md border border-black/10 bg-white px-2 py-1 text-xs font-semibold hover:bg-black/[0.03] disabled:opacity-40"
                      >
                        .md
                      </button>
                      {permissions.canManageContent ? (
                        <button
                          type="button"
                          onClick={() => void deleteDocuments([document])}
                          className="rounded-md border border-red-200 bg-white px-2 py-1 text-xs font-semibold text-red-600 hover:bg-red-50"
                        >
                          Delete
                        </button>
                      ) : null}
                    </div>
                  </div>
                );
              })
            )}
          </div>
          <div className="flex items-center justify-between border-t border-black/10 px-3 py-2 text-xs text-secondary">
            <span>Rows {documentWindow.total === 0 ? 0 : documentWindow.page * documentWindow.pageSize + 1}-{Math.min(documentWindow.total, (documentWindow.page + 1) * documentWindow.pageSize)} of {documentWindow.total}</span>
            <span className="flex items-center gap-2">
              <button type="button" disabled={documentWindow.page === 0} onClick={() => setDocumentPage((page) => Math.max(0, page - 1))} className="disabled:opacity-30">Previous</button>
              <button type="button" disabled={documentWindow.page + 1 >= documentWindow.totalPages} onClick={() => setDocumentPage((page) => page + 1)} className="disabled:opacity-30">Next</button>
            </span>
          </div>
        </main>
      </div>

      {preview ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="knowledge-preview-title"
            className="flex max-h-[86vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg bg-white shadow-2xl"
          >
            <div className="flex items-start justify-between gap-3 border-b border-black/10 p-4">
              <div className="min-w-0">
                <h2 id="knowledge-preview-title" className="text-base font-semibold">Preview document</h2>
                <p className="mt-0.5 truncate text-sm text-secondary">{preview.filename}</p>
              </div>
              <button
                type="button"
                onClick={() => setPreview(null)}
                className="rounded-md border border-black/10 px-2 py-1 text-sm font-semibold hover:bg-black/[0.04]"
              >
                Close
              </button>
            </div>
            <div className="min-h-[360px] overflow-auto p-4">
              {renderPreviewContent()}
            </div>
          </div>
        </div>
      ) : null}

      {notionOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg overflow-hidden rounded-lg bg-white shadow-2xl">
            <div className="border-b border-black/10 p-4">
              <h2 className="text-base font-semibold">Sync Notion</h2>
              <p className="text-sm text-secondary">{notionWorkspace}</p>
            </div>
            <div className="max-h-[48vh] overflow-y-auto p-3">
              {notionPages.length === 0 ? (
                <p className="p-3 text-sm text-secondary">No Notion pages found.</p>
              ) : (
                notionPages.map((page) => (
                  <label key={page.id} className="flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-black/[0.03]">
                    <input
                      type="checkbox"
                      checked={selectedNotionPages.has(page.id)}
                      onChange={() => {
                        setSelectedNotionPages((previous) => {
                          const next = new Set(previous);
                          if (next.has(page.id)) next.delete(page.id);
                          else next.add(page.id);
                          return next;
                        });
                      }}
                      className="h-4 w-4 rounded accent-primary"
                    />
                    <span className="truncate text-sm">{page.title || "Untitled"}</span>
                  </label>
                ))
              )}
            </div>
            <div className="flex justify-end gap-2 border-t border-black/10 p-4">
              <button type="button" onClick={() => setNotionOpen(false)} disabled={syncingNotion} className="rounded-lg px-3 py-2 text-sm font-semibold hover:bg-black/[0.04]">
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void syncNotion()}
                disabled={syncingNotion || selectedNotionPages.size === 0}
                className="rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white hover:bg-primary-light disabled:opacity-40"
              >
                {syncingNotion ? "Syncing..." : `Sync ${selectedNotionPages.size}`}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
