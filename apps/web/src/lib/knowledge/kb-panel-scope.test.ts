import { describe, expect, it } from "vitest";
import {
  buildKbPreviewUrl,
  getKbPanelDocumentRows,
  getKbPanelHiddenRowCount,
  getKbScopeBuckets,
  type KbScopedCollection,
} from "./kb-panel-scope";

const collections: KbScopedCollection[] = [
  {
    id: "personal-col",
    name: "Personal",
    scope: "personal",
    docs: [
      { id: "personal-doc-1", scope: "personal" },
      { id: "personal-doc-2", scope: "personal" },
    ],
  },
  {
    id: "org-col",
    name: "Org",
    scope: "org",
    orgId: "org-1",
    docs: [{ id: "org-doc-1", scope: "org", orgId: "org-1" }],
  },
  {
    id: "empty-org-col",
    name: "Empty Org",
    scope: "org",
    orgId: "org-1",
    docs: [],
  },
];

describe("KB panel scope helpers", () => {
  it("separates personal and org collections while preserving empty scoped collections", () => {
    const buckets = getKbScopeBuckets(collections);

    expect(buckets.personal.collections.map((col) => col.id)).toEqual(["personal-col"]);
    expect(buckets.personal.documentCount).toBe(2);
    expect(buckets.org.collections.map((col) => col.id)).toEqual(["org-col", "empty-org-col"]);
    expect(buckets.org.documentCount).toBe(1);
  });

  it("falls back to document scope for older collection data", () => {
    const buckets = getKbScopeBuckets([
      {
        id: "legacy-org-col",
        name: "Legacy Org",
        docs: [{ id: "org-doc-1", scope: "org", orgId: "org-1" }],
      },
    ]);

    expect(buckets.org.collections.map((col) => col.id)).toEqual(["legacy-org-col"]);
    expect(buckets.personal.collections).toEqual([]);
  });

  it("builds org preview URLs with explicit org scope", () => {
    expect(
      buildKbPreviewUrl({
        botId: "bot-1",
        doc: { id: "doc-1", scope: "org", orgId: "org-1" },
      }),
    ).toBe("/api/knowledge/documents/doc-1?botId=bot-1&type=converted&preview=true&scope=org&orgId=org-1");

    expect(
      buildKbPreviewUrl({
        botId: "bot-1",
        doc: { id: "doc-2", scope: "personal" },
      }),
    ).toBe("/api/knowledge/documents/doc-2?botId=bot-1&type=converted&preview=true");
  });

  it("returns indented document rows when collection documents have hierarchy", () => {
    const rows = getKbPanelDocumentRows({
      docs: [
        {
          id: "child",
          filename: "Child.md",
          scope: "org",
          parent_document_id: "root",
        },
        {
          id: "root",
          filename: "Root.md",
          scope: "org",
          parent_document_id: null,
        },
      ],
      collapsedIds: new Set(),
      search: "",
    });

    expect(rows.map((row) => [row.doc.id, row.depth, row.hasChildren])).toEqual([
      ["root", 0, true],
      ["child", 1, false],
    ]);
  });

  it("hides descendants for collapsed document rows outside search", () => {
    const rows = getKbPanelDocumentRows({
      docs: [
        { id: "child", filename: "Child.md", scope: "org", parent_document_id: "root" },
        { id: "root", filename: "Root.md", scope: "org", parent_document_id: null },
      ],
      collapsedIds: new Set(["root"]),
      search: "",
    });

    expect(rows.map((row) => row.doc.id)).toEqual(["root"]);
  });

  it("filters document tree rows by filename search and ignores collapsed rows while searching", () => {
    const rows = getKbPanelDocumentRows({
      docs: [
        { id: "child", filename: "Needle.md", scope: "org", parent_document_id: "root" },
        { id: "root", filename: "Root.md", scope: "org", parent_document_id: null },
      ],
      collapsedIds: new Set(["root"]),
      search: "needle",
    });

    expect(rows.map((row) => [row.doc.id, row.depth])).toEqual([["child", 0]]);
  });

  it("reports how many visible rows remain hidden beyond the initial limit", () => {
    const hiddenCount = getKbPanelHiddenRowCount({
      docs: [
        { id: "root-1", filename: "Root 1.md", scope: "org", parent_document_id: null },
        { id: "root-2", filename: "Root 2.md", scope: "org", parent_document_id: null },
        { id: "root-3", filename: "Root 3.md", scope: "org", parent_document_id: null },
      ],
      collapsedIds: new Set(),
      search: "",
      limit: 2,
    });

    expect(hiddenCount).toBe(1);
  });
});
