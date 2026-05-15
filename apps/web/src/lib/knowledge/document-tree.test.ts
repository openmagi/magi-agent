import { describe, expect, it } from "vitest";
import { flattenKnowledgeDocumentTree } from "./document-tree";

describe("knowledge document tree", () => {
  it("nests documents by explicit document parent ids", () => {
    const flattened = flattenKnowledgeDocumentTree([
      { id: "child", filename: "Child.md", parent_document_id: "root", sort_order: 2 },
      { id: "root", filename: "Root.md", parent_document_id: null, sort_order: 1 },
    ]);

    expect(flattened.map((entry) => [entry.doc.id, entry.depth])).toEqual([
      ["root", 0],
      ["child", 1],
    ]);
  });

  it("nests Notion pages by source parent page id when document parent ids are missing", () => {
    const flattened = flattenKnowledgeDocumentTree([
      { id: "child-doc", filename: "Sub Page.md", source_external_id: "page-child", source_parent_external_id: "page-root" },
      { id: "root-doc", filename: "Root Page.md", source_external_id: "page-root", source_parent_external_id: null },
    ]);

    expect(flattened.map((entry) => [entry.doc.filename, entry.depth])).toEqual([
      ["Root Page.md", 0],
      ["Sub Page.md", 1],
    ]);
  });

  it("can hide descendants of collapsed documents", () => {
    const flattened = flattenKnowledgeDocumentTree(
      [
        { id: "leaf", filename: "Leaf.md", parent_document_id: "child", sort_order: 3 },
        { id: "child", filename: "Child.md", parent_document_id: "root", sort_order: 2 },
        { id: "root", filename: "Root.md", parent_document_id: null, sort_order: 1 },
      ],
      new Set(["root"]),
    );

    expect(flattened.map((entry) => [entry.doc.id, entry.depth, entry.hasChildren])).toEqual([
      ["root", 0, true],
    ]);
  });
});
