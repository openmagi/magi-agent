import { describe, expect, it } from "vitest";
import {
  buildKnowledgePermissions,
  getPagedCollectionWindow,
  getPagedDocumentWindow,
  summarizeCollectionStatus,
  type KnowledgeConsoleCollection,
  type KnowledgeConsoleDocument,
} from "./console-model";

function collection(index: number): KnowledgeConsoleCollection {
  return {
    id: `col-${index}`,
    name: index === 127 ? "Needle Client Archive" : `Collection ${index}`,
    document_count: 100 + index,
    error_count: index % 10 === 0 ? 2 : 0,
    total_chunks: 1_000 + index,
    created_at: `2026-04-${String((index % 28) + 1).padStart(2, "0")}T00:00:00Z`,
  };
}

function document(index: number): KnowledgeConsoleDocument {
  return {
    id: `doc-${index}`,
    filename: index === 78 ? "Needle revenue workbook.xlsx" : `Document ${index}.pdf`,
    original_size: 1024 * index,
    converted_size: index % 2 === 0 ? 2048 : null,
    object_key_original: `original-${index}`,
    object_key_converted: index % 2 === 0 ? `converted-${index}.md` : null,
    chunk_count: index,
    status: index % 11 === 0 ? "error" : index % 7 === 0 ? "processing" : "ready",
    error_message: index % 11 === 0 ? "Parse failed" : null,
    created_at: `2026-04-${String((index % 28) + 1).padStart(2, "0")}T00:00:00Z`,
    source_provider: index % 5 === 0 ? "notion" : null,
  };
}

describe("knowledge console model", () => {
  it("returns bounded collection windows for large collection sets", () => {
    const collections = Array.from({ length: 300 }, (_, index) => collection(index));

    const page = getPagedCollectionWindow({
      collections,
      search: "",
      page: 2,
      pageSize: 50,
    });

    expect(page.rows).toHaveLength(50);
    expect(page.total).toBe(300);
    expect(page.totalPages).toBe(6);
    expect(page.rows[0]?.id).toBe("col-100");
  });

  it("filters collections before paging", () => {
    const collections = Array.from({ length: 300 }, (_, index) => collection(index));

    const page = getPagedCollectionWindow({
      collections,
      search: "needle",
      page: 0,
      pageSize: 50,
    });

    expect(page.rows.map((row) => row.name)).toEqual(["Needle Client Archive"]);
    expect(page.total).toBe(1);
  });

  it("returns bounded document windows with status and source filtering", () => {
    const documents = Array.from({ length: 240 }, (_, index) => document(index + 1));

    const page = getPagedDocumentWindow({
      documents,
      search: "",
      status: "ready",
      source: "file",
      page: 1,
      pageSize: 40,
    });

    expect(page.rows).toHaveLength(40);
    expect(page.total).toBeGreaterThan(40);
    expect(page.rows.every((row) => row.status === "ready")).toBe(true);
    expect(page.rows.every((row) => row.source_provider !== "notion")).toBe(true);
  });

  it("filters documents before paging", () => {
    const documents = Array.from({ length: 240 }, (_, index) => document(index + 1));

    const page = getPagedDocumentWindow({
      documents,
      search: "revenue",
      status: "all",
      source: "all",
      page: 0,
      pageSize: 40,
    });

    expect(page.rows.map((row) => row.filename)).toEqual(["Needle revenue workbook.xlsx"]);
    expect(page.total).toBe(1);
  });

  it("matches documents with Unicode-normalized aliases and paths", () => {
    const decomposed = "르챔버".normalize("NFD");
    const documents: KnowledgeConsoleDocument[] = [
      {
        id: "doc-korean",
        filename: `${decomposed}.md`,
        original_size: 1024,
        chunk_count: 2,
        status: "ready",
        created_at: "2026-04-26T00:00:00Z",
        path: `refs/${decomposed}.md`,
        aliases: [`${decomposed} 메뉴얼`],
      },
    ];

    const page = getPagedDocumentWindow({
      documents,
      search: "르챔버 메뉴얼",
      status: "all",
      source: "all",
      page: 0,
      pageSize: 10,
    });

    expect(page.rows.map((row) => row.id)).toEqual(["doc-korean"]);
  });

  it("matches documents by source system identifiers", () => {
    const documents: KnowledgeConsoleDocument[] = [
      {
        id: "doc-source",
        filename: "Ops Handbook.md",
        original_size: 2048,
        chunk_count: 8,
        status: "ready",
        created_at: "2026-04-26T00:00:00Z",
        source_provider: "notion",
        source_external_id: "notion-page-abc",
        source_parent_external_id: "notion-parent-xyz",
      },
    ];

    expect(
      getPagedDocumentWindow({
        documents,
        search: "notion-parent-xyz",
        status: "all",
        source: "all",
        page: 0,
        pageSize: 10,
      }).rows.map((row) => row.id),
    ).toEqual(["doc-source"]);

    expect(
      getPagedDocumentWindow({
        documents,
        search: "notion-page-abc",
        status: "all",
        source: "all",
        page: 0,
        pageSize: 10,
      }).rows.map((row) => row.id),
    ).toEqual(["doc-source"]);
  });

  it("summarizes collection status without negative ready counts", () => {
    expect(
      summarizeCollectionStatus({
        id: "empty",
        name: "Empty",
        document_count: 1,
        error_count: 3,
        total_chunks: 0,
        created_at: "2026-04-25T00:00:00Z",
      }),
    ).toEqual({
      readyCount: 0,
      errorCount: 3,
      chunkCount: 0,
      label: "0 ready · 3 failed · 0 chunks",
    });
  });

  it("allows org members to manage content but keeps Notion sync admin-only", () => {
    expect(buildKnowledgePermissions({ scope: "org", orgRole: "member" })).toEqual({
      canManageContent: true,
      canSyncNotion: false,
    });

    expect(buildKnowledgePermissions({ scope: "org", orgRole: "admin" })).toEqual({
      canManageContent: true,
      canSyncNotion: true,
    });

    expect(buildKnowledgePermissions({ scope: "personal" })).toEqual({
      canManageContent: true,
      canSyncNotion: true,
    });
  });
});
