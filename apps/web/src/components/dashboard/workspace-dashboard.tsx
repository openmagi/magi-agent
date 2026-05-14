import { useState, useMemo, useCallback } from "react";
import { Search } from "lucide-react";
import {
  DashboardPageHeader,
  DashboardCard,
  EmptyState,
  ButtonLike,
  formatFileSize,
} from "./shared";
import type { WorkspaceFileEntry } from "@/lib/workspace/workspace-files";

export interface WorkspaceDashboardProps {
  workspaceFiles: WorkspaceFileEntry[];
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onReadFile: (path: string) => Promise<string>;
  onSaveFile: (path: string, content: string) => Promise<void>;
}

export function WorkspaceDashboard({
  workspaceFiles,
  loading,
  refreshing,
  onRefresh,
  onReadFile,
  onSaveFile,
}: WorkspaceDashboardProps) {
  const [query, setQuery] = useState("");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [fileLoading, setFileLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const filteredFiles = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return workspaceFiles;
    return workspaceFiles.filter((file) =>
      file.path.toLowerCase().includes(normalized),
    );
  }, [query, workspaceFiles]);

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
        setError(err instanceof Error ? err.message : "Failed to read file");
      } finally {
        setFileLoading(false);
      }
    },
    [onReadFile],
  );

  const saveSelected = useCallback(async () => {
    if (!selectedPath) return;
    setSaving(true);
    setNotice(null);
    setError(null);
    try {
      await onSaveFile(selectedPath, editedContent);
      setContent(editedContent);
      setNotice("Workspace file saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save file");
    } finally {
      setSaving(false);
    }
  }, [editedContent, onSaveFile, selectedPath]);

  return (
    <div className="space-y-6">
      <DashboardPageHeader
        eyebrow="Operator Files"
        title="Workspace"
        description="Edit local prompts, contracts, harness rules, hooks, memory, compaction files, and artifacts."
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
        {/* File list */}
        <DashboardCard title="Files">
          <div className="relative mb-4">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-secondary/40" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter files..."
              className="min-h-11 w-full rounded-xl border border-black/[0.08] bg-white pl-10 pr-3.5 py-2.5 text-sm outline-none transition focus:border-primary/40 focus:ring-4 focus:ring-primary/10"
            />
          </div>
          {loading ? (
            <EmptyState>Loading workspace...</EmptyState>
          ) : filteredFiles.length === 0 ? (
            <EmptyState>No editable workspace files found.</EmptyState>
          ) : (
            <div className="max-h-[620px] divide-y divide-black/[0.06] overflow-y-auto rounded-xl border border-black/[0.08]">
              {filteredFiles.slice(0, 160).map((file) => (
                <button
                  key={file.path}
                  type="button"
                  onClick={() => void openFile(file.path)}
                  className={`block min-h-14 w-full cursor-pointer px-4 py-3 text-left transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/30 ${selectedPath === file.path ? "bg-primary/[0.06]" : "bg-white"}`}
                >
                  <div className="truncate text-sm font-semibold text-foreground">
                    {file.path}
                  </div>
                  <div className="mt-1 text-xs text-secondary">
                    {formatFileSize(file.size ?? 0)}
                    {file.modifiedAt ? ` · ${file.modifiedAt}` : ""}
                  </div>
                </button>
              ))}
            </div>
          )}
        </DashboardCard>

        {/* Editor */}
        <DashboardCard
          title={selectedPath ?? "Workspace File"}
          action={
            selectedPath ? (
              <ButtonLike
                onClick={() => void saveSelected()}
                disabled={
                  saving || fileLoading || editedContent === content
                }
              >
                {saving ? "Saving..." : "Save"}
              </ButtonLike>
            ) : null
          }
        >
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
          {!selectedPath ? (
            <EmptyState>
              Select a workspace file to view or edit it.
            </EmptyState>
          ) : fileLoading ? (
            <EmptyState>Loading file...</EmptyState>
          ) : (
            <textarea
              value={editedContent}
              onChange={(event) => setEditedContent(event.target.value)}
              spellCheck={false}
              className="h-[520px] w-full resize-none rounded-xl border border-black/[0.08] bg-white px-4 py-3 font-mono text-sm leading-6 text-foreground outline-none transition focus:border-primary/40 focus:ring-4 focus:ring-primary/10"
            />
          )}
        </DashboardCard>
      </div>
    </div>
  );
}
