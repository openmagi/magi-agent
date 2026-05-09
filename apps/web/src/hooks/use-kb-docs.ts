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
