import { describe, expect, it } from "vitest";
import {
  buildKbCollectionWithDocs,
  resolveKbCollectionScope,
} from "./use-kb-docs";

describe("useKbDocs scope helpers", () => {
  it("preserves org scope when loading org collections into the chat KB panel", () => {
    const scope = resolveKbCollectionScope(
      {
        id: "org-col-1",
        name: "Shared Files",
        scope: "org",
        org_id: "org-1",
        document_count: 1,
        total_chunks: 12,
      },
      "bot-1",
    );

    expect(scope).toEqual({ kind: "org", orgId: "org-1" });
  });

  it("tags mapped collection docs with org scope and hierarchy fields", () => {
    const collection = buildKbCollectionWithDocs(
      {
        id: "org-col-1",
        name: "Shared Files",
        scope: "org",
        org_id: "org-1",
        document_count: 1,
        total_chunks: 12,
      },
      [
        {
          id: "doc-1",
          filename: "Quarterly Review.pptx",
          original_size: 1024,
          converted_size: 512,
          chunk_count: 4,
          status: "ready",
          collection_id: "org-col-1",
          parent_document_id: "root-doc",
          path: "Decks/Quarterly Review.pptx",
          sort_order: 2,
          source_external_id: "page-1",
          source_parent_external_id: "page-root",
        },
      ],
      { kind: "org", orgId: "org-1" },
    );

    expect(collection).toEqual({
      id: "org-col-1",
      name: "Shared Files",
      scope: "org",
      orgId: "org-1",
      docs: [
        {
          id: "doc-1",
          filename: "Quarterly Review.pptx",
          status: "ready",
          scope: "org",
          orgId: "org-1",
          parent_document_id: "root-doc",
          path: "Decks/Quarterly Review.pptx",
          sort_order: 2,
          source_external_id: "page-1",
          source_parent_external_id: "page-root",
          collectionId: "org-col-1",
          collectionName: "Shared Files",
        },
      ],
    });
  });
});
