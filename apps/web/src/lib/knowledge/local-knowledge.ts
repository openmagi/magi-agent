import type { KbCollectionWithDocs } from "@/hooks/use-kb-docs";

/**
 * Shape of the local Python runtime's `GET /v1/app/knowledge` response. The
 * endpoint scans `<workspace>/knowledge/` and `<workspace>/.magi/knowledge/`,
 * treating each immediate subdirectory as a collection.
 */
export interface LocalKnowledgeCollection {
  name: string;
  path: string;
  documentCount: number;
  sizeBytes: number;
}

export interface LocalKnowledgeDocument {
  collection: string;
  filename: string;
  title: string;
  path: string;
  sizeBytes: number;
  mtimeMs: number;
}

export interface LocalKnowledgeIndex {
  collections?: LocalKnowledgeCollection[];
  documents?: LocalKnowledgeDocument[];
}

/**
 * Maps the local workspace knowledge index into the panel's
 * `KbCollectionWithDocs` shape. The local KB has no hosted "org" notion, so
 * every collection is surfaced under the "personal" scope. The document `path`
 * (workspace-relative) doubles as the stable id and the locator used to fetch
 * preview content via `GET /v1/app/knowledge/file?path=`.
 */
export function mapLocalKnowledgeIndex(
  index: LocalKnowledgeIndex,
): KbCollectionWithDocs[] {
  const collections = index.collections ?? [];
  const documents = index.documents ?? [];

  return collections.map((col) => ({
    id: col.name,
    name: col.name,
    scope: "personal" as const,
    orgId: null,
    docs: documents
      .filter((doc) => doc.collection === col.name)
      .map((doc) => ({
        id: doc.path,
        filename: doc.filename,
        status: "ready",
        scope: "personal" as const,
        orgId: null,
        parent_document_id: null,
        path: doc.path,
        sort_order: null,
        source_external_id: null,
        source_parent_external_id: null,
        collectionId: col.name,
        collectionName: col.name,
      })),
  }));
}
