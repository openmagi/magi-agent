"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { KnowledgeConsole } from "@/components/knowledge/knowledge-console";
import {
  buildKnowledgePermissions,
  type KnowledgeConsoleCollection,
  type KnowledgeConsoleDocument,
} from "@/lib/knowledge/console-model";
import {
  deleteKnowledgeConsoleCollection,
  deleteKnowledgeConsoleDocuments,
  downloadKnowledgeDocument,
  fetchKnowledgeDocumentPreview,
  openKnowledgeNotion,
  syncKnowledgeNotion,
  uploadKnowledgeConsoleFiles,
} from "@/lib/knowledge/console-client-actions";

interface CollectionsPayload {
  collections?: KnowledgeConsoleCollection[];
  kb_used_bytes?: number;
  kb_quota_bytes?: number;
}

interface DocumentsPayload {
  documents?: KnowledgeConsoleDocument[];
}

interface CollectionPayload {
  collection?: KnowledgeConsoleCollection;
  error?: string;
}

async function readError(response: Response, fallback: string): Promise<string> {
  const data = await response.json().catch(() => null) as { error?: string } | null;
  return data?.error || fallback;
}

export default function KnowledgePage(): React.ReactElement {
  const searchParams = useSearchParams();
  const viewAs = searchParams.get("viewAs");
  const viewAsSuffix = viewAs ? `viewAs=${encodeURIComponent(viewAs)}` : "";

  const [collections, setCollections] = useState<KnowledgeConsoleCollection[]>([]);
  const [selectedCollection, setSelectedCollection] = useState<string | null>(null);
  const [documents, setDocuments] = useState<KnowledgeConsoleDocument[]>([]);
  const [loadingCollections, setLoadingCollections] = useState(true);
  const [loadingDocuments, setLoadingDocuments] = useState(false);
  const [kbUsedBytes, setKbUsedBytes] = useState(0);
  const [kbQuotaBytes, setKbQuotaBytes] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);

  const fetcher = useCallback((url: string, init?: RequestInit) => fetch(url, init), []);

  const fetchCollections = useCallback(async () => {
    setLoadingCollections(true);
    setLoadError(null);
    try {
      const response = await fetch(`/api/knowledge/collections${viewAsSuffix ? `?${viewAsSuffix}` : ""}`);
      if (!response.ok) {
        throw new Error(await readError(response, "Failed to load collections"));
      }

      const data = await response.json() as CollectionsPayload;
      const nextCollections = data.collections || [];
      setCollections(nextCollections);
      setKbUsedBytes(data.kb_used_bytes || 0);
      setKbQuotaBytes(data.kb_quota_bytes || 0);
      setSelectedCollection((current) => {
        if (current && nextCollections.some((collection) => collection.name === current)) return current;
        return nextCollections[0]?.name ?? null;
      });
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Failed to load collections");
    } finally {
      setLoadingCollections(false);
    }
  }, [viewAsSuffix]);

  const fetchDocuments = useCallback(async (collectionName: string) => {
    setLoadingDocuments(true);
    try {
      const response = await fetch(
        `/api/knowledge/documents?collection=${encodeURIComponent(collectionName)}${viewAsSuffix ? `&${viewAsSuffix}` : ""}`,
      );
      if (!response.ok) {
        throw new Error(await readError(response, "Failed to load documents"));
      }

      const data = await response.json() as DocumentsPayload;
      setDocuments(data.documents || []);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Failed to load documents");
      setDocuments([]);
    } finally {
      setLoadingDocuments(false);
    }
  }, [viewAsSuffix]);

  useEffect(() => {
    void fetchCollections();
  }, [fetchCollections]);

  useEffect(() => {
    if (!selectedCollection) {
      setDocuments([]);
      return;
    }

    void fetchDocuments(selectedCollection);
  }, [fetchDocuments, selectedCollection]);

  useEffect(() => {
    const hasProcessing = documents.some((document) => document.status === "processing" || document.status === "pending");
    if (!hasProcessing || !selectedCollection) return;

    const interval = window.setInterval(() => {
      void fetchDocuments(selectedCollection);
      void fetchCollections();
    }, 180000); // 3 min — manual refresh button available for immediate check

    return () => window.clearInterval(interval);
  }, [documents, fetchCollections, fetchDocuments, selectedCollection]);

  const permissions = useMemo(
    () => buildKnowledgePermissions({ scope: "personal" }),
    [],
  );

  return (
    <div className="space-y-3">
      {loadError ? (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">{loadError}</p>
      ) : null}
      <KnowledgeConsole
        title="Knowledge Base"
        description="Organize the files and Notion pages every bot in your account can search."
        scope="personal"
        collections={collections}
        documents={documents}
        selectedCollectionName={selectedCollection}
        permissions={permissions}
        loadingCollections={loadingCollections}
        loadingDocuments={loadingDocuments}
        quota={kbQuotaBytes > 0 ? { usedBytes: kbUsedBytes, quotaBytes: kbQuotaBytes } : null}
        onSelectCollection={(collection) => setSelectedCollection((current) => current === collection.name ? null : collection.name)}
        onCreateCollection={async (name) => {
          const response = await fetch("/api/knowledge/collections", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name }),
          });
          const data = await response.json().catch(() => null) as CollectionPayload | null;
          if (!response.ok) {
            throw new Error(data?.error || "Failed to create collection");
          }

          const collectionName = data?.collection?.name || name;
          setSelectedCollection(collectionName);
          await fetchCollections();
        }}
        onUploadFiles={async (collectionName, files, onProgress) => {
          const result = await uploadKnowledgeConsoleFiles({
            fetcher,
            scope: "personal",
            collectionName,
            files,
            onProgress,
          });
          await fetchCollections();
          await fetchDocuments(collectionName);
          return result;
        }}
        onDeleteDocuments={async (targets) => {
          const result = await deleteKnowledgeConsoleDocuments({
            fetcher,
            scope: "personal",
            documents: targets,
          });
          await fetchCollections();
          if (selectedCollection) await fetchDocuments(selectedCollection);
          return result;
        }}
        onDeleteCollection={async (collection) => {
          await deleteKnowledgeConsoleCollection({
            fetcher,
            scope: "personal",
            collectionId: collection.id,
          });
          setSelectedCollection(null);
          await fetchCollections();
        }}
        onOpenNotion={() => openKnowledgeNotion({ fetcher, scope: "personal" })}
        onSyncNotion={async (collectionName, pageIds) => {
          const result = await syncKnowledgeNotion({
            fetcher,
            scope: "personal",
            collectionName,
            pageIds,
          });
          await fetchCollections();
          await fetchDocuments(collectionName);
          return result;
        }}
        onFetchPreview={(document) =>
          fetchKnowledgeDocumentPreview({
            fetcher,
            scope: "personal",
            document,
          })
        }
        onDownloadDocument={(document, type) =>
          downloadKnowledgeDocument({
            fetcher,
            scope: "personal",
            document,
            type,
          })
        }
      />
    </div>
  );
}
