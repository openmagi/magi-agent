import { useState, useMemo, useCallback } from "react";
import { Search, Trash2, Archive, RefreshCw } from "lucide-react";
import {
  DashboardPageHeader,
  DashboardCard,
  MetricTile,
  EmptyState,
  ButtonLike,
  formatFileSize,
  asString,
  asRecord,
  asArray,
  type JsonRecord,
} from "./shared";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface MemoryFileEntry {
  path: string;
  sizeBytes: number;
  mtimeMs: number | null;
}

interface MemorySearchResult {
  path?: string;
  score?: number;
  contentPreview?: string;
  context?: string;
}

/* ------------------------------------------------------------------ */
/*  MemoryDashboard                                                    */
/* ------------------------------------------------------------------ */

export interface MemoryDashboardProps {
  memoryFiles: MemoryFileEntry[];
  memoryStatus: JsonRecord | null;
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onSearch: (query: string) => Promise<JsonRecord>;
  onReadFile: (path: string) => Promise<string>;
  onSaveFile: (path: string, content: string) => Promise<void>;
  onDeleteFiles: (paths: string[]) => Promise<void>;
  onCompact: () => Promise<void>;
  onReindex: () => Promise<void>;
}

export function MemoryDashboard({
  memoryFiles,
  memoryStatus,
  loading,
  refreshing,
  onRefresh,
  onSearch,
  onReadFile,
  onSaveFile,
  onDeleteFiles,
  onCompact,
  onReindex,
}: MemoryDashboardProps) {
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<MemorySearchResult[]>([]);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(
    () => new Set(),
  );
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [fileLoading, setFileLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const queryLower = query.trim().toLowerCase();
  const filteredFiles = useMemo(() => {
    if (!queryLower) return memoryFiles;
    return memoryFiles.filter((file) =>
      file.path.toLowerCase().includes(queryLower),
    );
  }, [memoryFiles, queryLower]);
  const rootMemory = asRecord(memoryStatus?.rootMemory);

  const openFile = useCallback(
    async (path: string) => {
      setSelectedPath(path);
      setFileLoading(true);
      setNotice(null);
      setError(null);
      try {
        const nextContent = await onReadFile(path);
        setContent(nextContent);
        setEditedContent(nextContent);
      } catch (err) {
        setContent("");
        setEditedContent("");
        setError(
          err instanceof Error ? err.message : "Failed to read memory file",
        );
      } finally {
        setFileLoading(false);
      }
    },
    [onReadFile],
  );

  const runSearch = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed) {
      setSearchResults([]);
      return;
    }
    setSearching(true);
    setError(null);
    try {
      const payload = await onSearch(trimmed);
      setSearchResults(asArray(payload.results) as MemorySearchResult[]);
    } catch (err) {
      setSearchResults([]);
      setError(
        err instanceof Error ? err.message : "Memory search failed",
      );
    } finally {
      setSearching(false);
    }
  }, [onSearch, query]);

  const toggleSelected = useCallback((path: string) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const deletePaths = useCallback(
    async (paths: string[]) => {
      if (paths.length === 0) return;
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        await onDeleteFiles(paths);
        setSelectedPaths((prev) => {
          const next = new Set(prev);
          for (const path of paths) next.delete(path);
          return next;
        });
        if (selectedPath && paths.includes(selectedPath)) {
          setSelectedPath(null);
          setContent("");
          setEditedContent("");
        }
        setNotice(
          `${paths.length} memory file${paths.length === 1 ? "" : "s"} deleted`,
        );
        onRefresh();
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to delete memory files",
        );
      } finally {
        setBusy(false);
      }
    },
    [onDeleteFiles, onRefresh, selectedPath],
  );

  const saveSelected = useCallback(async () => {
    if (!selectedPath) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await onSaveFile(selectedPath, editedContent);
      setContent(editedContent);
      setNotice("Memory file saved");
      onRefresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to save memory file",
      );
    } finally {
      setBusy(false);
    }
  }, [editedContent, onRefresh, onSaveFile, selectedPath]);

  const runMemoryAction = useCallback(
    async (action: "compact" | "reindex") => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        if (action === "compact") await onCompact();
        else await onReindex();
        setNotice(
          action === "compact"
            ? "Compaction triggered"
            : "Memory index refreshed",
        );
        onRefresh();
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Memory operation failed",
        );
      } finally {
        setBusy(false);
      }
    },
    [onCompact, onRefresh, onReindex],
  );

  const selectedCount = selectedPaths.size;
  const dailyPaths = memoryFiles
    .map((file) => file.path)
    .filter((path) => path.startsWith("memory/daily/"));

  return (
    <div className="space-y-6">
      <DashboardPageHeader
        eyebrow="Hipocampus"
        title="Memory"
        description="Browse, search, edit, compact, and reindex local memory used by the runtime."
        action={
          <ButtonLike
            variant="secondary"
            onClick={onRefresh}
            disabled={refreshing}
          >
            {refreshing ? "Refreshing..." : "Refresh"}
          </ButtonLike>
        }
      />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        {/* Left: file list + actions */}
        <DashboardCard title="Memory Files">
          <div className="mb-4 grid gap-3 sm:grid-cols-2">
            <MetricTile label="Files" value={memoryFiles.length} />
            <MetricTile
              label="QMD"
              value={memoryStatus?.qmdReady === true ? "Ready" : "Local"}
            />
          </div>
          {asString(rootMemory.path) && (
            <div className="mb-4 rounded-xl bg-gray-50 px-4 py-3 text-xs text-secondary">
              Root: {asString(rootMemory.path)}
            </div>
          )}

          {/* Search bar */}
          <div className="mb-4 flex gap-2">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-secondary/40" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void runSearch();
                }}
                placeholder="Search memory..."
                className="min-h-11 w-full rounded-xl border border-black/[0.08] bg-white pl-10 pr-3.5 py-2.5 text-sm outline-none transition focus:border-primary/40 focus:ring-4 focus:ring-primary/10"
              />
            </div>
            <ButtonLike
              onClick={() => void runSearch()}
              disabled={searching}
              className="px-4"
            >
              {searching ? "..." : "Search"}
            </ButtonLike>
          </div>

          {/* Action buttons */}
          <div className="mb-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void deletePaths(Array.from(selectedPaths))}
              disabled={busy || selectedCount === 0}
              className="inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-lg bg-gray-100 px-3 text-xs font-semibold text-foreground transition-colors hover:bg-gray-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Delete selected{selectedCount > 0 ? ` (${selectedCount})` : ""}
            </button>
            <button
              type="button"
              onClick={() => void deletePaths(dailyPaths)}
              disabled={busy || dailyPaths.length === 0}
              className="inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-lg bg-gray-100 px-3 text-xs font-semibold text-foreground transition-colors hover:bg-gray-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Clear daily logs
            </button>
            <button
              type="button"
              onClick={() => void runMemoryAction("compact")}
              disabled={busy}
              className="inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-lg bg-gray-100 px-3 text-xs font-semibold text-foreground transition-colors hover:bg-gray-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:opacity-50"
            >
              <Archive className="h-3.5 w-3.5" />
              Compact
            </button>
            <button
              type="button"
              onClick={() => void runMemoryAction("reindex")}
              disabled={busy}
              className="inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-lg bg-gray-100 px-3 text-xs font-semibold text-foreground transition-colors hover:bg-gray-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:opacity-50"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Reindex
            </button>
          </div>

          {notice && (
            <div className="mb-3 rounded-xl bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
              {notice}
            </div>
          )}
          {error && (
            <div className="mb-3 rounded-xl bg-red-50 px-3 py-2 text-xs text-red-500">
              {error}
            </div>
          )}

          {/* File list */}
          {loading ? (
            <EmptyState>Loading memory...</EmptyState>
          ) : filteredFiles.length === 0 ? (
            <EmptyState>No memory files found.</EmptyState>
          ) : (
            <div className="max-h-[520px] divide-y divide-black/[0.06] overflow-y-auto rounded-xl border border-black/[0.08]">
              {filteredFiles.map((file) => {
                const checked = selectedPaths.has(file.path);
                const active = selectedPath === file.path;
                return (
                  <div
                    key={file.path}
                    className={`flex min-h-14 items-center gap-3 px-3 py-2 transition-colors ${active ? "bg-primary/[0.06]" : "bg-white"}`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleSelected(file.path)}
                      className="h-4 w-4 cursor-pointer rounded border-black/[0.12]"
                      aria-label={`Select ${file.path}`}
                    />
                    <button
                      type="button"
                      onClick={() => void openFile(file.path)}
                      className="min-w-0 flex-1 cursor-pointer rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                    >
                      <div className="truncate text-sm font-semibold text-foreground">
                        {file.path}
                      </div>
                      <div className="mt-0.5 text-xs text-secondary">
                        {formatFileSize(file.sizeBytes)}
                        {file.mtimeMs
                          ? ` · ${new Date(file.mtimeMs).toISOString()}`
                          : ""}
                      </div>
                    </button>
                    <button
                      type="button"
                      onClick={() => void deletePaths([file.path])}
                      disabled={busy}
                      className="min-h-8 cursor-pointer rounded-lg px-2 text-xs font-semibold text-red-500 transition-colors hover:bg-red-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/20 disabled:opacity-50"
                    >
                      Delete
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </DashboardCard>

        {/* Right: editor + search results */}
        <div className="space-y-5">
          <DashboardCard
            title={selectedPath ?? "Memory File"}
            action={
              selectedPath ? (
                <ButtonLike
                  onClick={() => void saveSelected()}
                  disabled={
                    busy || fileLoading || editedContent === content
                  }
                  className="min-h-9 px-3 py-1.5"
                >
                  Save
                </ButtonLike>
              ) : null
            }
          >
            {!selectedPath ? (
              <EmptyState>
                Select a memory file to view or edit it.
              </EmptyState>
            ) : fileLoading ? (
              <EmptyState>Loading file...</EmptyState>
            ) : (
              <textarea
                value={editedContent}
                onChange={(event) => setEditedContent(event.target.value)}
                spellCheck={false}
                className="h-[420px] w-full resize-none rounded-xl border border-black/[0.08] bg-white px-4 py-3 font-mono text-sm leading-6 text-foreground outline-none transition focus:border-primary/40 focus:ring-4 focus:ring-primary/10"
              />
            )}
          </DashboardCard>

          {searchResults.length > 0 && (
            <DashboardCard title="Search Results">
              <div className="space-y-2">
                {searchResults.map((result, index) => (
                  <button
                    key={`${result.path ?? "result"}-${index}`}
                    type="button"
                    onClick={() => result.path && void openFile(result.path)}
                    className="block min-h-14 w-full cursor-pointer rounded-xl bg-gray-50 px-4 py-3 text-left transition-colors hover:bg-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="truncate text-sm font-semibold text-foreground">
                        {result.path ?? `result-${index + 1}`}
                      </div>
                      {typeof result.score === "number" && (
                        <div className="shrink-0 text-xs text-secondary">
                          {result.score.toFixed(2)}
                        </div>
                      )}
                    </div>
                    {result.contentPreview && (
                      <div className="mt-1 line-clamp-3 text-xs leading-5 text-secondary">
                        {result.contentPreview}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            </DashboardCard>
          )}
        </div>
      </div>
    </div>
  );
}
