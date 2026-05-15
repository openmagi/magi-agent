import React from "react";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { KnowledgeConsole } from "./knowledge-console";
import type {
  KnowledgeConsoleCollection,
  KnowledgeConsoleDocument,
  KnowledgePermissions,
} from "@/lib/knowledge/console-model";

function collections(count: number): KnowledgeConsoleCollection[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `col-${index}`,
    name: index === 0 ? "Downloads" : `Collection ${index}`,
    document_count: 10 + index,
    error_count: index % 5 === 0 ? 1 : 0,
    total_chunks: 100 + index,
    created_at: "2026-04-25T00:00:00Z",
  }));
}

function documents(count: number): KnowledgeConsoleDocument[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `doc-${index}`,
    filename: index === 0 ? "르챔버 2025 매출.xlsx" : `Document ${index}.pdf`,
    original_size: 1024 * (index + 1),
    converted_size: index % 2 === 0 ? 2048 : null,
    object_key_original: `original-${index}`,
    object_key_converted: index % 2 === 0 ? `converted-${index}.md` : null,
    chunk_count: index + 1,
    status: index % 13 === 0 ? "processing" : "ready",
    error_message: null,
    created_at: "2026-04-25T00:00:00Z",
  }));
}

function renderConsole({
  permissions,
}: {
  permissions: KnowledgePermissions;
}): string {
  return renderToStaticMarkup(
    <KnowledgeConsole
      title="Organization KB"
      description="Shared knowledge base"
      scope="org"
      collections={collections(120)}
      documents={documents(160)}
      selectedCollectionName="Downloads"
      permissions={permissions}
      loadingCollections={false}
      loadingDocuments={false}
      quota={null}
      onSelectCollection={() => {}}
      onCreateCollection={() => Promise.resolve()}
      onUploadFiles={() => Promise.resolve({ uploaded: 0, failures: [] })}
      onDeleteDocuments={() => Promise.resolve({ deleted: 0, failures: [] })}
      onDeleteCollection={() => Promise.resolve()}
      onOpenNotion={() => Promise.resolve({ workspace: "Notion", pages: [] })}
      onSyncNotion={() => Promise.resolve({ synced: 0, errors: [] })}
      onFetchPreview={() => Promise.resolve({ filename: "Preview.md", content: "# Preview", mode: "markdown" })}
      onDownloadDocument={() => Promise.resolve()}
    />,
  );
}

describe("KnowledgeConsole", () => {
  it("renders bounded collection and document windows for large data sets", () => {
    const html = renderConsole({
      permissions: { canManageContent: true, canSyncNotion: true },
    });

    expect(html).toContain("120 collections");
    expect(html).toContain("160 documents");
    expect(html).toContain("Rows 1-50 of 160");
    expect(html).toContain("Collections 1-40 of 120");
    expect(html).toContain("Document 1.pdf");
    expect(html).toContain("Original</button>");
    expect(html).toContain("Markdown</button>");
    expect(html).not.toContain("Collection 70");
    expect(html).not.toContain("Document 90.pdf");
  });

  it("keeps document version actions in each row without an expanded detail area", () => {
    const html = renderConsole({
      permissions: { canManageContent: true, canSyncNotion: true },
    });

    expect(html).not.toContain("lg:grid-cols-[260px_minmax(420px,1fr)_320px]");
    expect(html).not.toContain("Selected document");
    expect(html).not.toContain("Preview area");
    expect(html).not.toContain("No preview loaded.");
    expect(html).toContain("Preview markdown");
    expect(html).toContain("Download original");
    expect(html).toContain("Download markdown");
  });

  it("keeps document preview UI in an overlay instead of the file list", () => {
    const source = readFileSync(new URL("./knowledge-console.tsx", import.meta.url), "utf8");

    expect(source).toContain('role="dialog"');
    expect(source).toContain("fixed inset-0");
    expect(source).toContain("Preview document");
  });

  it("wires the download callback into the console component scope", () => {
    const source = readFileSync(new URL("./knowledge-console.tsx", import.meta.url), "utf8");

    expect(source).toMatch(/export function KnowledgeConsole\(\{[\s\S]*onDownloadDocument,[\s\S]*\}: KnowledgeConsoleProps\)/);
  });

  it("renders per-file upload progress bars from upload state", () => {
    const source = readFileSync(new URL("./knowledge-console.tsx", import.meta.url), "utf8");

    expect(source).toContain("uploadItems");
    expect(source).toContain('role="progressbar"');
    expect(source).toContain("aria-valuenow");
  });

  it("lets org members manage content while hiding admin-only Notion sync", () => {
    const html = renderConsole({
      permissions: { canManageContent: true, canSyncNotion: false },
    });

    expect(html).toContain("New Collection");
    expect(html).toContain("Upload");
    expect(html).toContain("Notion sync requires admin");
    expect(html).not.toContain("Sync Notion</button>");
  });

  it("shows Notion sync as an enabled action for admins", () => {
    const html = renderConsole({
      permissions: { canManageContent: true, canSyncNotion: true },
    });

    expect(html).toContain("Sync Notion</button>");
    expect(html).not.toContain("Notion sync requires admin");
  });
});
