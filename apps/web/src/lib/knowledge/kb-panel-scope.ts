import {
  flattenKnowledgeDocumentTree,
  type FlattenedKnowledgeDocument,
  type KnowledgeDocumentTreeFields,
} from "./document-tree";

export type KbPanelScope = "personal" | "org";

export interface KbScopedDoc {
  id: string;
  filename?: string;
  scope: KbPanelScope;
  orgId?: string | null;
  parent_document_id?: string | null;
  sort_order?: number | null;
  source_external_id?: string | null;
  source_parent_external_id?: string | null;
}

export interface KbScopedCollection<TDoc extends KbScopedDoc = KbScopedDoc> {
  id: string;
  name: string;
  scope?: KbPanelScope;
  orgId?: string | null;
  docs: TDoc[];
}

export interface KbScopeBucket<TCollection extends KbScopedCollection = KbScopedCollection> {
  collections: TCollection[];
  documentCount: number;
}

function getKbPanelFlattenedRows<TDoc extends KbScopedDoc & KnowledgeDocumentTreeFields>({
  docs,
  collapsedIds,
  search,
}: {
  docs: TDoc[];
  collapsedIds: Set<string>;
  search: string;
}): Array<FlattenedKnowledgeDocument<TDoc>> {
  const searchLower = search.trim().toLowerCase();
  const filteredDocs = searchLower
    ? docs.filter((doc) => doc.filename.toLowerCase().includes(searchLower))
    : docs;
  const effectiveCollapsedIds = searchLower ? new Set<string>() : collapsedIds;

  return flattenKnowledgeDocumentTree(filteredDocs, effectiveCollapsedIds);
}

export function getKbCollectionScope(collection: KbScopedCollection): KbPanelScope {
  return collection.scope ?? collection.docs[0]?.scope ?? "personal";
}

export function getKbScopeBuckets<TCollection extends KbScopedCollection>(
  collections: TCollection[],
): Record<KbPanelScope, KbScopeBucket<TCollection>> {
  const buckets: Record<KbPanelScope, KbScopeBucket<TCollection>> = {
    personal: { collections: [], documentCount: 0 },
    org: { collections: [], documentCount: 0 },
  };

  for (const collection of collections) {
    const scope = getKbCollectionScope(collection);
    buckets[scope].collections.push(collection);
    buckets[scope].documentCount += collection.docs.length;
  }

  return buckets;
}

export function getDefaultKbPanelScope<TCollection extends KbScopedCollection>(
  collections: TCollection[],
): KbPanelScope {
  const buckets = getKbScopeBuckets(collections);
  if (buckets.personal.collections.length > 0) return "personal";
  if (buckets.org.collections.length > 0) return "org";
  return "personal";
}

export function buildKbPreviewUrl({
  botId,
  doc,
}: {
  botId: string;
  doc: KbScopedDoc;
}): string {
  const params = new URLSearchParams({
    botId,
    type: "converted",
    preview: "true",
  });

  if (doc.scope === "org" && doc.orgId) {
    params.set("scope", "org");
    params.set("orgId", doc.orgId);
  }

  return `/api/knowledge/documents/${doc.id}?${params.toString()}`;
}

export function getKbPanelDocumentRows<TDoc extends KbScopedDoc & KnowledgeDocumentTreeFields>({
  docs,
  collapsedIds,
  search,
  limit = 20,
}: {
  docs: TDoc[];
  collapsedIds: Set<string>;
  search: string;
  limit?: number | null;
}): Array<FlattenedKnowledgeDocument<TDoc>> {
  const rows = getKbPanelFlattenedRows({
    docs,
    collapsedIds,
    search,
  });

  if (limit == null) return rows;
  return rows.slice(0, limit);
}

export function getKbPanelHiddenRowCount<TDoc extends KbScopedDoc & KnowledgeDocumentTreeFields>({
  docs,
  collapsedIds,
  search,
  limit = 20,
}: {
  docs: TDoc[];
  collapsedIds: Set<string>;
  search: string;
  limit?: number | null;
}): number {
  if (limit == null) return 0;

  const totalRows = getKbPanelFlattenedRows({
    docs,
    collapsedIds,
    search,
  }).length;

  return Math.max(totalRows - limit, 0);
}
