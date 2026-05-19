"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAgentFetch } from "@/lib/local-api";

interface LocalKnowledgeCollection {
  name: string;
  path: string;
  documentCount: number;
  sizeBytes: number;
}

interface LocalKnowledgeDocument {
  collection: string;
  filename: string;
  title: string;
  path: string;
  sizeBytes: number;
  mtimeMs: number;
  score?: number;
  snippet?: string;
}

interface KnowledgePayload {
  collections?: LocalKnowledgeCollection[];
  documents?: LocalKnowledgeDocument[];
  results?: LocalKnowledgeDocument[];
  content?: string;
  path?: string;
}

function formatBytes(value: number | undefined): string {
  const bytes = Math.max(0, value ?? 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function defaultDocumentPath(collection: string | null): string {
  const prefix = collection && collection !== "default" ? `${collection}/` : "";
  return `${prefix}notes.md`;
}

async function parseJson(response: Response): Promise<KnowledgePayload> {
  const data = (await response.json().catch(() => null)) as KnowledgePayload | null;
  if (!response.ok) {
    throw new Error((data as { error?: string } | null)?.error ?? `Request failed: ${response.status}`);
  }
  return data ?? {};
}

export default function KnowledgePage(): React.ReactElement {
  const agentFetch = useAgentFetch();
  const [collections, setCollections] = useState<LocalKnowledgeCollection[]>([]);
  const [documents, setDocuments] = useState<LocalKnowledgeDocument[]>([]);
  const [selectedCollection, setSelectedCollection] = useState<string | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [draft, setDraft] = useState("");
  const [newPath, setNewPath] = useState(defaultDocumentPath(null));
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const filteredDocuments = useMemo(() => documents, [documents]);

  const loadKnowledge = useCallback(async (collection?: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (collection) params.set("collection", collection);
      const data = await parseJson(
        await agentFetch(`/v1/app/knowledge${params.size > 0 ? `?${params.toString()}` : ""}`),
      );
      setCollections(data.collections ?? []);
      setDocuments(data.documents ?? []);
      setSelectedCollection(collection ?? null);
      setNewPath(defaultDocumentPath(collection ?? null));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load local knowledge");
    } finally {
      setLoading(false);
    }
  }, [agentFetch]);

  useEffect(() => {
    void loadKnowledge(null);
  }, [loadKnowledge]);

  const openDocument = useCallback(async (path: string) => {
    setError(null);
    setSuccess(null);
    try {
      const params = new URLSearchParams({ path });
      const data = await parseJson(await agentFetch(`/v1/app/knowledge/file?${params.toString()}`));
      const nextContent = data.content ?? "";
      setSelectedPath(data.path ?? path);
      setContent(nextContent);
      setDraft(nextContent);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to read knowledge document");
    }
  }, [agentFetch]);

  const search = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed) {
      await loadKnowledge(selectedCollection);
      return;
    }
    setSearching(true);
    setError(null);
    try {
      const params = new URLSearchParams({ q: trimmed, limit: "25" });
      if (selectedCollection) params.set("collection", selectedCollection);
      const data = await parseJson(await agentFetch(`/v1/app/knowledge/search?${params.toString()}`));
      setDocuments(data.results ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to search local knowledge");
    } finally {
      setSearching(false);
    }
  }, [agentFetch, loadKnowledge, query, selectedCollection]);

  const saveDocument = useCallback(async () => {
    const path = selectedPath ?? newPath.trim();
    if (!path) return;
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const data = await parseJson(await agentFetch("/v1/app/knowledge/file", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, content: draft }),
      }));
      const writtenPath = data.path ?? path;
      setSelectedPath(writtenPath);
      setContent(draft);
      setSuccess(`Saved ${writtenPath}`);
      await loadKnowledge(selectedCollection);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save knowledge document");
    } finally {
      setSaving(false);
    }
  }, [agentFetch, draft, loadKnowledge, newPath, selectedCollection, selectedPath]);

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Local Knowledge</h1>
          <p className="text-secondary mt-1">
            Manage searchable files stored under the local workspace <code>knowledge/</code> directory.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => loadKnowledge(selectedCollection)} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh"}
        </Button>
      </div>

      {error ? (
        <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
      ) : null}
      {success ? (
        <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{success}</p>
      ) : null}

      <div className="grid grid-cols-1 xl:grid-cols-[320px_minmax(0,1fr)] gap-6">
        <div className="space-y-4">
          <GlassCard>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-foreground">Collections</h2>
              <span className="text-xs text-muted">{collections.length}</span>
            </div>
            <div className="space-y-1">
              <button
                type="button"
                onClick={() => loadKnowledge(null)}
                className={`w-full rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                  selectedCollection === null ? "bg-primary/10 text-primary-light" : "hover:bg-gray-100"
                }`}
              >
                All documents
              </button>
              {collections.map((collection) => (
                <button
                  key={collection.name}
                  type="button"
                  onClick={() => loadKnowledge(collection.name)}
                  className={`w-full rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                    selectedCollection === collection.name ? "bg-primary/10 text-primary-light" : "hover:bg-gray-100"
                  }`}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate">{collection.name}</span>
                    <span className="text-xs text-muted">{collection.documentCount}</span>
                  </span>
                </button>
              ))}
            </div>
          </GlassCard>

          <GlassCard>
            <h2 className="text-sm font-semibold text-foreground mb-3">Search</h2>
            <div className="flex gap-2">
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void search();
                }}
                placeholder="Search local knowledge..."
              />
              <Button variant="primary" size="sm" onClick={search} disabled={searching}>
                {searching ? "..." : "Go"}
              </Button>
            </div>
          </GlassCard>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[minmax(280px,380px)_minmax(0,1fr)] gap-4 min-h-[680px]">
          <GlassCard className="overflow-hidden !p-0">
            <div className="border-b border-gray-200 px-4 py-3">
              <h2 className="text-sm font-semibold text-foreground">Documents</h2>
            </div>
            <div className="max-h-[620px] overflow-y-auto p-2">
              {loading ? (
                <p className="p-3 text-sm text-secondary">Loading...</p>
              ) : filteredDocuments.length === 0 ? (
                <p className="p-3 text-sm text-secondary">No knowledge files found.</p>
              ) : (
                filteredDocuments.map((document) => (
                  <button
                    key={document.path}
                    type="button"
                    onClick={() => openDocument(document.path)}
                    className={`w-full rounded-lg px-3 py-2 text-left transition-colors ${
                      selectedPath === document.path ? "bg-primary/10 text-primary-light" : "hover:bg-gray-100"
                    }`}
                  >
                    <p className="truncate text-sm font-medium">{document.title || document.filename}</p>
                    <p className="truncate text-xs text-secondary">{document.path}</p>
                    <p className="mt-1 text-xs text-muted">
                      {document.collection} · {formatBytes(document.sizeBytes)}
                    </p>
                    {document.snippet ? (
                      <p className="mt-1 line-clamp-2 text-xs text-secondary">{document.snippet}</p>
                    ) : null}
                  </button>
                ))
              )}
            </div>
          </GlassCard>

          <GlassCard className="flex flex-col overflow-hidden !p-0">
            <div className="border-b border-gray-200 px-4 py-3">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold text-foreground">
                    {selectedPath ?? "New document"}
                  </h2>
                  <p className="text-xs text-secondary">
                    {selectedPath ? "Editing existing local knowledge file" : "Create a file under knowledge/"}
                  </p>
                </div>
                <Button variant="primary" size="sm" onClick={saveDocument} disabled={saving}>
                  {saving ? "Saving..." : "Save"}
                </Button>
              </div>
              {!selectedPath ? (
                <div className="mt-3">
                  <Input
                    value={newPath}
                    onChange={(event) => setNewPath(event.target.value)}
                    placeholder="collection/file.md"
                  />
                </div>
              ) : null}
            </div>
            <textarea
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value);
                setSuccess(null);
              }}
              className="min-h-[560px] flex-1 resize-none border-0 bg-transparent p-4 font-mono text-sm leading-relaxed text-foreground outline-none"
              placeholder="# Notes&#10;Write local knowledge content here."
              spellCheck={false}
            />
            {selectedPath && content !== draft ? (
              <div className="border-t border-gray-200 px-4 py-2 text-xs text-amber-600">
                Unsaved changes
              </div>
            ) : null}
          </GlassCard>
        </div>
      </div>
    </div>
  );
}
