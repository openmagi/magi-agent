"use client";

import { useEffect, useState, useCallback } from "react";

interface KbCollection {
  id: string;
  name: string;
  scope?: "personal" | "org";
  org_id?: string | null;
  document_count: number;
  total_chunks: number;
}

interface KbDocument {
  id: string;
  filename: string;
  original_size: number | null;
  converted_size: number | null;
  chunk_count: number;
  status: string;
  collection_id: string;
  parent_document_id?: string | null;
  path?: string | null;
  sort_order?: number | null;
  source_external_id?: string | null;
  source_parent_external_id?: string | null;
}

export interface KbDocEntry {
  id: string;
  filename: string;
  status: string;
  scope: "personal" | "org";
  orgId?: string | null;
  parent_document_id?: string | null;
  path?: string | null;
  sort_order?: number | null;
  source_external_id?: string | null;
  source_parent_external_id?: string | null;
  collectionId: string;
  collectionName: string;
}

export interface KbCollectionWithDocs {
  id: string;
  name: string;
  scope: "personal" | "org";
  orgId?: string | null;
  docs: KbDocEntry[];
}

type KbRequestScope =
  | { kind: "personal"; botId: string }
  | { kind: "org"; orgId: string };

function buildKbDocumentsUrl({
  collectionName,
  botId,
  scope,
}: {
  collectionName: string;
  botId: string;
  scope: KbRequestScope;
}): string {
  const params = new URLSearchParams({ collection: collectionName });
  if (scope.kind === "org") {
    params.set("scope", "org");
    params.set("orgId", scope.orgId);
  } else {
    params.set("botId", botId);
  }
  return `/api/knowledge/documents?${params.toString()}`;
}

export function resolveKbCollectionScope(collection: KbCollection, botId: string): KbRequestScope {
  if (collection.scope === "org" && collection.org_id) {
    return { kind: "org", orgId: collection.org_id };
  }
  return { kind: "personal", botId };
}

export function buildKbCollectionWithDocs(
  collection: KbCollection,
  documents: KbDocument[],
  scope: KbRequestScope,
): KbCollectionWithDocs {
  return {
    id: collection.id,
    name: collection.name,
    scope: scope.kind,
    orgId: scope.kind === "org" ? scope.orgId : null,
    docs: documents.map((doc) => ({
      id: doc.id,
      filename: doc.filename,
      status: doc.status,
      scope: scope.kind,
      orgId: scope.kind === "org" ? scope.orgId : null,
      parent_document_id: doc.parent_document_id ?? null,
      path: doc.path ?? null,
      sort_order: doc.sort_order ?? null,
      source_external_id: doc.source_external_id ?? null,
      source_parent_external_id: doc.source_parent_external_id ?? null,
      collectionId: collection.id,
      collectionName: collection.name,
    })),
  };
}

const AUTO_REFRESH_MS = 300_000; // 5 minutes

/**
 * Fetches KB collections and their documents for a given bot.
 * Auto-refreshes every 30s. Manual refresh via `refresh()`.
 */
export function useKbDocs(botId: string): {
  collections: KbCollectionWithDocs[];
  allDocs: KbDocEntry[];
  loading: boolean;
  refreshing: boolean;
  refresh: () => void;
} {
  const [collections, setCollections] = useState<KbCollectionWithDocs[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  useEffect(() => {
    let cancelled = false;
    const isInitial = collections.length === 0;

    async function load(): Promise<void> {
      if (isInitial) setLoading(true);
      else setRefreshing(true);
      try {
        const colRes = await fetch(`/api/knowledge/collections?botId=${botId}`);
        if (!colRes.ok) return;
        const colData = await colRes.json();
        const cols: KbCollection[] = [
          ...(colData.collections ?? []),
          ...(colData.org_collections ?? []),
        ];

        const results: KbCollectionWithDocs[] = [];
        for (const col of cols) {
          const scope = resolveKbCollectionScope(col, botId);
          const docRes = await fetch(buildKbDocumentsUrl({
            collectionName: col.name,
            botId,
            scope,
          }));
          if (!docRes.ok) continue;
          const docData = await docRes.json();
          results.push(buildKbCollectionWithDocs(col, docData.documents ?? [], scope));
        }

        if (!cancelled) setCollections(results);
      } catch (err) {
        console.error("[use-kb-docs] Failed to load:", err);
      } finally {
        if (!cancelled) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    }

    load();

    // Auto-refresh every 30s
    const interval = setInterval(load, AUTO_REFRESH_MS);
    return () => { cancelled = true; clearInterval(interval); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId, refreshKey]);

  const allDocs = collections.flatMap((c) => c.docs);

  return { collections, allDocs, loading, refreshing, refresh };
}
