import { useState, useCallback } from "react";
import { Upload } from "lucide-react";
import {
  DashboardPageHeader,
  DashboardCard,
  EmptyState,
  ButtonLike,
} from "./shared";
import type { KbCollectionWithDocs } from "@/hooks/use-kb-docs";

export interface KnowledgeDashboardProps {
  kbCollections: KbCollectionWithDocs[];
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onUpload: (files: FileList) => Promise<void>;
}

export function KnowledgeDashboard({
  kbCollections,
  loading,
  refreshing,
  onRefresh,
  onUpload,
}: KnowledgeDashboardProps) {
  const [uploading, setUploading] = useState(false);
  const [uploadNotice, setUploadNotice] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const docs = kbCollections.flatMap((collection) =>
    collection.docs.map((doc) => ({ ...doc, collectionName: collection.name })),
  );

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      setUploading(true);
      setUploadNotice(null);
      setUploadError(null);
      try {
        await onUpload(files);
        setUploadNotice(
          `${files.length} file${files.length === 1 ? "" : "s"} added to local KB`,
        );
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : "Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [onUpload],
  );

  return (
    <div className="max-w-4xl space-y-6">
      <DashboardPageHeader
        eyebrow="Workspace KB"
        title="Knowledge"
        description="Upload and inspect local documents that the self-hosted runtime can search during a mission."
        action={
          <ButtonLike variant="secondary" onClick={onRefresh} disabled={refreshing}>
            {refreshing ? "Refreshing..." : "Refresh"}
          </ButtonLike>
        }
      />
      <DashboardCard title="Workspace Knowledge">
        {/* Upload zone */}
        <div className="mb-5 rounded-xl border border-dashed border-primary/25 bg-primary/[0.04] px-4 py-6 transition-colors hover:border-primary/40 hover:bg-primary/[0.06]">
          <label className="block cursor-pointer text-center">
            <input
              type="file"
              multiple
              className="hidden"
              onChange={(event) => void handleFiles(event.target.files)}
            />
            <Upload className="mx-auto mb-2 h-6 w-6 text-primary/60" />
            <span className="text-sm font-semibold text-primary">
              {uploading ? "Uploading..." : "Upload local knowledge"}
            </span>
            <span className="mt-1 block text-xs text-secondary">
              PDF, DOCX, XLSX, PPTX, HTML, CSV, TXT, MD, JSON, ZIP, and other
              supported files.
            </span>
          </label>
        </div>
        {uploadNotice && (
          <div className="mb-3 rounded-xl bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
            {uploadNotice}
          </div>
        )}
        {uploadError && (
          <div className="mb-3 rounded-xl bg-red-50 px-3 py-2 text-xs text-red-500">
            {uploadError}
          </div>
        )}
        {loading ? (
          <EmptyState>Loading knowledge...</EmptyState>
        ) : docs.length === 0 ? (
          <EmptyState>No local KB documents yet.</EmptyState>
        ) : (
          <div className="divide-y divide-black/[0.06] overflow-hidden rounded-xl border border-black/[0.08]">
            {docs.map((doc) => (
              <div
                key={`${doc.collectionName}:${doc.id}`}
                className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-gray-50/80"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-semibold text-foreground">
                    {doc.filename}
                  </div>
                  <div className="mt-1 text-xs text-secondary">
                    {doc.collectionName}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </DashboardCard>
    </div>
  );
}
