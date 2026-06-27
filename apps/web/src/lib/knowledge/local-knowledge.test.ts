import { describe, expect, it } from "vitest";

import { mapLocalKnowledgeIndex } from "./local-knowledge";

describe("mapLocalKnowledgeIndex", () => {
  it("groups documents under their collection as personal scope", () => {
    const result = mapLocalKnowledgeIndex({
      collections: [
        { name: "notes", path: "knowledge/notes", documentCount: 2, sizeBytes: 100 },
        { name: "specs", path: "knowledge/specs", documentCount: 1, sizeBytes: 50 },
      ],
      documents: [
        { collection: "notes", filename: "a.md", title: "a", path: "knowledge/notes/a.md", sizeBytes: 60, mtimeMs: 2 },
        { collection: "notes", filename: "b.md", title: "b", path: "knowledge/notes/b.md", sizeBytes: 40, mtimeMs: 1 },
        { collection: "specs", filename: "c.md", title: "c", path: "knowledge/specs/c.md", sizeBytes: 50, mtimeMs: 3 },
      ],
    });

    expect(result).toHaveLength(2);
    const notes = result.find((c) => c.name === "notes")!;
    expect(notes.scope).toBe("personal");
    expect(notes.orgId).toBeNull();
    expect(notes.docs.map((d) => d.filename)).toEqual(["a.md", "b.md"]);
    // path doubles as the stable id / locator used for preview fetch.
    expect(notes.docs[0].id).toBe("knowledge/notes/a.md");
    expect(notes.docs[0].path).toBe("knowledge/notes/a.md");
    expect(notes.docs[0].collectionName).toBe("notes");

    const specs = result.find((c) => c.name === "specs")!;
    expect(specs.docs).toHaveLength(1);
  });

  it("handles empty/missing fields without throwing", () => {
    expect(mapLocalKnowledgeIndex({})).toEqual([]);
    expect(mapLocalKnowledgeIndex({ collections: [], documents: [] })).toEqual([]);
  });

  it("emits a collection with no docs when documents are absent", () => {
    const result = mapLocalKnowledgeIndex({
      collections: [{ name: "empty", path: "knowledge/empty", documentCount: 0, sizeBytes: 0 }],
    });
    expect(result).toEqual([
      { id: "empty", name: "empty", scope: "personal", orgId: null, docs: [] },
    ]);
  });
});
