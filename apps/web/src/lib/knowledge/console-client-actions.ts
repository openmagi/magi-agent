import type { KnowledgeConsoleDocument, KnowledgeScope } from "@/lib/knowledge/console-model";
import {
  KB_UPLOAD_EXTENSIONS,
  getKnowledgeUploadExtension,
  prepareKnowledgeUploadFile,
  resolveKnowledgeUploadMimeType,
  uploadKnowledgeFileToSignedUrl,
} from "@/lib/knowledge/upload-mime";

export interface KnowledgeActionFailure {
  filename: string;
  reason: string;
}

export interface KnowledgeActionResult {
  uploaded?: number;
  deleted?: number;
  failures: KnowledgeActionFailure[];
}

export type KnowledgeUploadProgressPhase = "queued" | "uploading" | "indexing" | "ready" | "failed";

export interface KnowledgeUploadProgressUpdate {
  index: number;
  filename: string;
  phase: KnowledgeUploadProgressPhase;
  progress: number;
  message?: string;
}

type KnowledgeFetch = (url: string, init?: RequestInit) => Promise<Response>;

interface ScopeParams {
  scope: KnowledgeScope;
  botId?: string;
  orgId?: string;
}

interface UploadFilesParams extends ScopeParams {
  fetcher: KnowledgeFetch;
  collectionName: string;
  files: File[];
  onProgress?: (update: KnowledgeUploadProgressUpdate) => void;
}

interface DeleteDocumentsParams extends ScopeParams {
  fetcher: KnowledgeFetch;
  documents: KnowledgeConsoleDocument[];
}

interface PreviewDocumentParams extends ScopeParams {
  fetcher: KnowledgeFetch;
  document: KnowledgeConsoleDocument;
}

interface DownloadDocumentParams extends PreviewDocumentParams {
  type: "original" | "converted";
}

interface NotionParams extends ScopeParams {
  fetcher: KnowledgeFetch;
}

interface SyncNotionParams extends NotionParams {
  collectionName: string;
  pageIds: string[];
}

interface ErrorPayload {
  error?: string;
}

interface SignedUploadPayload {
  upload_url?: string;
  signedUrl?: string;
  storage_path?: string;
}

interface PreviewPayload {
  filename?: string;
  content?: string;
}

interface NotionPagesPayload {
  workspace?: string;
  pages?: Array<{ id: string; title: string }>;
}

interface NotionSyncPayload {
  synced?: number;
  errors?: string[];
}

interface BatchDeletePayload {
  deleted?: number;
  failures?: KnowledgeActionFailure[];
  errors?: Array<{ id?: string; filename?: string; error?: string; reason?: string }>;
}

async function responseError(response: Response, fallback: string): Promise<string> {
  const data = await response.json().catch(() => null) as ErrorPayload | null;
  return data?.error || fallback;
}

function addScopeParams(params: URLSearchParams, scopeParams: ScopeParams): void {
  if (scopeParams.scope === "org") {
    params.set("scope", "org");
    if (scopeParams.orgId) params.set("orgId", scopeParams.orgId);
    return;
  }

  if (scopeParams.botId) params.set("botId", scopeParams.botId);
}

function withScopeBody(scopeParams: ScopeParams, body: Record<string, unknown>): Record<string, unknown> {
  if (scopeParams.scope === "org") {
    return {
      ...body,
      scope: "org",
      orgId: scopeParams.orgId,
    };
  }

  return {
    ...body,
    botId: scopeParams.botId,
  };
}

function isCsvLike(filename: string): boolean {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  return ext === "csv" || ext === "tsv";
}

function downloadNameFromDisposition(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;

  const utfMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utfMatch?.[1]) return decodeURIComponent(utfMatch[1]);

  const quotedMatch = disposition.match(/filename="?([^";]+)"?/i);
  if (quotedMatch?.[1]) return decodeURIComponent(quotedMatch[1]);

  return fallback;
}

export async function uploadKnowledgeConsoleFiles({
  fetcher,
  collectionName,
  files,
  scope,
  botId,
  orgId,
  onProgress,
}: UploadFilesParams): Promise<{ uploaded: number; failures: KnowledgeActionFailure[] }> {
  const failures: KnowledgeActionFailure[] = [];
  const validFiles = files.map((file, index) => ({ file, index })).filter(({ file, index }) => {
    const supported = KB_UPLOAD_EXTENSIONS.has(getKnowledgeUploadExtension(file.name));
    if (!supported) {
      failures.push({ filename: file.name, reason: "Unsupported file type" });
      onProgress?.({
        index,
        filename: file.name,
        phase: "failed",
        progress: 100,
        message: "Unsupported file type",
      });
    }
    return supported;
  });

  let uploaded = 0;

  for (const { file, index } of validFiles) {
    const uploadFile = prepareKnowledgeUploadFile(file);
    const contentType = resolveKnowledgeUploadMimeType(uploadFile);
    const report = (
      phase: KnowledgeUploadProgressPhase,
      progress: number,
      message?: string,
    ) => {
      onProgress?.({
        index,
        filename: file.name,
        phase,
        progress: Math.max(0, Math.min(100, progress)),
        message,
      });
    };

    report("queued", 0);

    const signedUrlResponse = await fetcher("/api/knowledge/upload-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(withScopeBody({ scope, botId, orgId }, {
        collection: collectionName,
        filename: file.name,
        content_type: contentType,
      })),
    });

    if (!signedUrlResponse.ok) {
      const reason = await responseError(signedUrlResponse, "Failed to prepare upload");
      failures.push({
        filename: file.name,
        reason,
      });
      report("failed", 100, reason);
      continue;
    }

    const signedUrlData = await signedUrlResponse.json().catch(() => null) as SignedUploadPayload | null;
    const uploadUrl = signedUrlData?.upload_url || signedUrlData?.signedUrl;
    const storagePath = signedUrlData?.storage_path;
    if (!uploadUrl || !storagePath) {
      const reason = "Upload URL response was incomplete";
      failures.push({ filename: file.name, reason });
      report("failed", 100, reason);
      continue;
    }

    try {
      report("uploading", 2);
      await uploadKnowledgeFileToSignedUrl(uploadUrl, uploadFile, ({ percent }) => {
        report("uploading", Math.max(2, Math.min(85, Math.round(percent * 0.85))));
      });
    } catch (error) {
      const reason = error instanceof Error ? error.message : "Storage upload failed";
      failures.push({
        filename: file.name,
        reason,
      });
      report("failed", 100, reason);
      continue;
    }

    report("indexing", 90);
    const ingestResponse = await fetcher("/api/knowledge/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(withScopeBody({ scope, botId, orgId }, {
        collection: collectionName,
        filename: file.name,
        mime_type: contentType,
        storage_path: storagePath,
      })),
    });

    if (!ingestResponse.ok) {
      const reason = await responseError(ingestResponse, "Failed to index file");
      failures.push({
        filename: file.name,
        reason,
      });
      report("failed", 100, reason);
      continue;
    }

    uploaded += 1;
    report("ready", 100);
  }

  return { uploaded, failures };
}

interface DeleteCollectionParams extends ScopeParams {
  fetcher: KnowledgeFetch;
  collectionId: string;
}

export async function deleteKnowledgeConsoleCollection({
  fetcher,
  collectionId,
  scope,
  botId,
  orgId,
}: DeleteCollectionParams): Promise<void> {
  const params = new URLSearchParams();
  addScopeParams(params, { scope, botId, orgId });

  const response = await fetcher(`/api/knowledge/collections/${collectionId}?${params.toString()}`, {
    method: "DELETE",
  });

  if (!response.ok) {
    throw new Error(await responseError(response, "Failed to delete collection"));
  }
}

export async function deleteKnowledgeConsoleDocuments({
  fetcher,
  documents,
  scope,
  botId,
  orgId,
}: DeleteDocumentsParams): Promise<{ deleted: number; failures: KnowledgeActionFailure[] }> {
  if (documents.length === 0) {
    return { deleted: 0, failures: [] };
  }

  const response = await fetcher("/api/knowledge/documents/batch-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(withScopeBody({ scope, botId, orgId }, {
      doc_ids: documents.map((document) => document.id),
    })),
  });

  if (!response.ok) {
    throw new Error(await responseError(response, "Failed to delete documents"));
  }

  const data = await response.json().catch(() => null) as BatchDeletePayload | null;
  const filenameById = new Map(documents.map((document) => [document.id, document.filename]));
  const failures = data?.failures ?? (data?.errors ?? []).map((failure) => ({
    filename: failure.filename || (failure.id ? filenameById.get(failure.id) : undefined) || "Document",
    reason: failure.reason || failure.error || "Failed to delete document",
  }));

  return { deleted: data?.deleted ?? 0, failures };
}

export async function fetchKnowledgeDocumentPreview({
  fetcher,
  document,
  scope,
  botId,
  orgId,
}: PreviewDocumentParams): Promise<{ filename: string; content: string; mode: "markdown" | "csv" }> {
  const mode = isCsvLike(document.filename) ? "csv" : "markdown";
  const params = new URLSearchParams({
    type: mode === "csv" ? "original" : "converted",
    preview: "true",
  });
  addScopeParams(params, { scope, botId, orgId });

  const response = await fetcher(`/api/knowledge/documents/${document.id}?${params.toString()}`);
  if (!response.ok) {
    throw new Error(await responseError(response, "Preview failed"));
  }

  const data = await response.json().catch(() => null) as PreviewPayload | null;
  return {
    filename: data?.filename || document.filename,
    content: data?.content || "",
    mode,
  };
}

export async function downloadKnowledgeDocument({
  fetcher,
  document,
  type,
  scope,
  botId,
  orgId,
}: DownloadDocumentParams): Promise<void> {
  const params = new URLSearchParams({ type });
  addScopeParams(params, { scope, botId, orgId });

  const response = await fetcher(`/api/knowledge/documents/${document.id}?${params.toString()}`);
  if (!response.ok) {
    throw new Error(await responseError(response, "Download failed"));
  }

  const blob = await response.blob();
  const fallback = type === "converted" ? `${document.filename}.md` : document.filename;
  const filename = downloadNameFromDisposition(response.headers.get("Content-Disposition"), fallback);
  const url = URL.createObjectURL(blob);
  const anchor = window.document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  window.document.body.appendChild(anchor);
  anchor.click();
  window.setTimeout(() => {
    anchor.remove();
    URL.revokeObjectURL(url);
  }, 0);
}

export async function openKnowledgeNotion({
  fetcher,
  scope,
  botId,
  orgId,
}: NotionParams): Promise<{ workspace: string; pages: Array<{ id: string; title: string }> }> {
  const params = new URLSearchParams();
  addScopeParams(params, { scope, botId, orgId });

  const response = await fetcher(`/api/knowledge/notion-sync?${params.toString()}`);
  if (!response.ok) {
    throw new Error(await responseError(response, "Failed to load Notion pages"));
  }

  const data = await response.json().catch(() => null) as NotionPagesPayload | null;
  return {
    workspace: data?.workspace || "Notion",
    pages: data?.pages || [],
  };
}

export async function syncKnowledgeNotion({
  fetcher,
  collectionName,
  pageIds,
  scope,
  botId,
  orgId,
}: SyncNotionParams): Promise<{ synced: number; errors: string[] }> {
  const response = await fetcher("/api/knowledge/notion-sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(withScopeBody({ scope, botId, orgId }, {
      collection: collectionName,
      page_ids: pageIds,
    })),
  });

  if (!response.ok) {
    throw new Error(await responseError(response, "Notion sync failed"));
  }

  const data = await response.json().catch(() => null) as NotionSyncPayload | null;
  return {
    synced: data?.synced || 0,
    errors: data?.errors || [],
  };
}
