"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import { useMessages } from "@/lib/i18n";
import {
  CONVERTER_ACCEPTED_EXTENSIONS,
  getConverterSourceFormat,
  getConverterTargetFormats,
  type ConverterSourceFormat,
} from "@/lib/converter/formats";

interface ConversionJob {
  id: string;
  source_filename: string;
  source_mime: string;
  target_format: string;
  status: string;
  progress_pct: number;
  progress_message: string | null;
  result_filename: string | null;
  error_message: string | null;
  credits_actual: number | null;
  created_at: string;
  completed_at: string | null;
}

const FORMAT_LABELS: Record<string, string> = { hwpx: "HWPX", docx: "DOCX", pdf: "PDF" };

const ACCEPTED_EXTS = new Set(CONVERTER_ACCEPTED_EXTENSIONS.map((ext) => ext.slice(1)));

function getSourceFormat(mime: string, filename: string): ConverterSourceFormat | "unsupported" {
  return getConverterSourceFormat(mime, filename) ?? "unsupported";
}

function getAvailableTargets(sourceFormat: string): string[] {
  if (sourceFormat === "pdf" || sourceFormat === "docx") {
    return getConverterTargetFormats(sourceFormat);
  }
  return [];
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function statusLabel(status: string, t: ReturnType<typeof useMessages>): string {
  const map: Record<string, string> = {
    pending: t.converter.statusPending,
    parsing: t.converter.statusParsing,
    ocr: t.converter.statusOcr,
    converting: t.converter.statusConverting,
    rendering: t.converter.statusRendering,
    completed: t.converter.statusCompleted,
    failed: t.converter.statusFailed,
  };
  return map[status] || status;
}

function filterValidFiles(files: File[]): File[] {
  return files.filter((f) => {
    const ext = f.name.split(".").pop()?.toLowerCase() || "";
    return ACCEPTED_EXTS.has(ext) && f.size <= 50 * 1024 * 1024;
  });
}

function getCommonTargets(files: File[]): string[] {
  if (files.length === 0) return [];

  const formats = new Set(files.map((f) => getSourceFormat(f.type, f.name)));
  let targets: string[] | null = null;
  for (const fmt of formats) {
    const available = getAvailableTargets(fmt);
    targets = targets ? targets.filter((target) => available.includes(target)) : available;
  }
  return targets ?? [];
}

export default function ConverterPage() {
  const params = useParams();
  const botId = params.botId as string;
  const t = useMessages();

  const [jobs, setJobs] = useState<ConversionJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Bulk file selection
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [targetFormat, setTargetFormat] = useState<string>("docx");
  const [selectedModel, setSelectedModel] = useState<string>("claude-opus-4-6");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const fetchJobs = useCallback(async () => {
    try {
      const res = await fetch(`/api/converter/jobs?botId=${botId}`);
      if (res.ok) {
        const data = (await res.json()) as { jobs: ConversionJob[] };
        setJobs(data.jobs || []);
      }
    } finally {
      setLoading(false);
    }
  }, [botId]);

  useEffect(() => { fetchJobs(); }, [fetchJobs]);

  useEffect(() => {
    const hasActive = jobs.some((j) => !["completed", "failed"].includes(j.status));
    if (!hasActive) return;
    const interval = setInterval(fetchJobs, 2000);
    return () => clearInterval(interval);
  }, [jobs, fetchJobs]);

  function handleFilesSelect(files: File[]) {
    const valid = filterValidFiles(files);
    if (valid.length === 0) return;
    setSelectedFiles((prev) => [...prev, ...valid]);
    setError(null);
    setSuccess(null);
    // Set target format based on first file if not yet set
    if (selectedFiles.length === 0 && valid.length > 0) {
      const fmt = getSourceFormat(valid[0].type, valid[0].name);
      const targets = getAvailableTargets(fmt);
      setTargetFormat(targets[0] || "docx");
    }
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const items = e.dataTransfer.items;
    const files: File[] = [];

    // Handle both files and folder drops
    if (items) {
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === "file") {
          const file = item.getAsFile();
          if (file) files.push(file);
        }
      }
    } else {
      for (let i = 0; i < e.dataTransfer.files.length; i++) {
        files.push(e.dataTransfer.files[i]);
      }
    }
    handleFilesSelect(files);
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    if (!e.target.files) return;
    const files = Array.from(e.target.files);
    handleFilesSelect(files);
    e.target.value = "";
  }

  function removeFile(idx: number) {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== idx));
  }

  function clearFiles() {
    setSelectedFiles([]);
  }

  async function handleBulkUpload() {
    if (selectedFiles.length === 0) return;
    if (!targetFormat || !getCommonTargets(selectedFiles).includes(targetFormat)) {
      setError("Unsupported conversion. Supported: PDF to DOCX/HWPX, DOCX to HWPX.");
      return;
    }
    setUploading(true);
    setError(null);
    setSuccess(null);

    let uploaded = 0;
    let failed = 0;
    const total = selectedFiles.length;

    for (const file of selectedFiles) {
      setUploadProgress(`Uploading ${file.name} (${uploaded + failed + 1}/${total})...`);

      try {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("target_format", targetFormat);
        formData.append("bot_id", botId);
        formData.append("model", selectedModel);

        const res = await fetch("/api/converter/upload", { method: "POST", body: formData });
        if (res.ok) {
          uploaded++;
        } else {
          failed++;
        }
      } catch {
        failed++;
      }
    }

    setSelectedFiles([]);
    setUploadProgress("");
    setUploading(false);

    if (uploaded > 0) {
      setSuccess(`${uploaded} file(s) submitted for conversion${failed > 0 ? ` (${failed} failed)` : ""}`);
    } else {
      setError(`All ${total} uploads failed`);
    }

    void fetchJobs();
  }

  async function handleDownload(jobId: string) {
    const res = await fetch(`/api/converter/jobs/${jobId}/download`);
    if (!res.ok) return;
    const data = (await res.json()) as { url?: string; filename?: string };
    if (data.url) {
      const a = document.createElement("a");
      a.href = data.url;
      a.download = data.filename ?? "converted";
      a.click();
    }
  }

  async function handleDelete(jobId: string) {
    await fetch(`/api/converter/jobs/${jobId}`, { method: "DELETE" });
    void fetchJobs();
  }

  const allTargets = getCommonTargets(selectedFiles);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">{t.converter.title}</h1>
        <p className="mt-1 text-sm text-neutral-500">{t.converter.subtitle}</p>
      </div>

      {/* Status Messages */}
      {error && <p className="text-sm text-red-500">{error}</p>}
      {success && <p className="text-sm text-green-600">{success}</p>}

      {/* Upload Zone */}
      <div className="rounded-xl border border-neutral-200 bg-white p-6 dark:border-neutral-800 dark:bg-neutral-900">
        <div
          className={`flex min-h-[120px] cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed p-8 text-center transition-colors ${
            dragOver
              ? "border-purple-400 bg-purple-50 dark:bg-purple-950/20"
              : "border-neutral-300 hover:border-purple-400 dark:border-neutral-700 dark:hover:border-purple-500"
          }`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          {/* Hidden inputs: file multi-select + folder */}
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx"
            multiple
            className="hidden"
            onChange={handleInputChange}
          />
          <input
            ref={folderInputRef}
            type="file"
            // @ts-expect-error webkitdirectory is not in React types
            webkitdirectory=""
            accept=".pdf,.docx"
            className="hidden"
            onChange={handleInputChange}
          />

          <div className="text-neutral-400">
            <span className="mb-2 block text-3xl leading-none">+</span>
            <p className="text-sm font-medium">{t.converter.dropzoneText}</p>
            <p className="mt-1 text-xs text-neutral-400">{t.converter.dropzoneFormats}</p>
          </div>
        </div>

        {/* Folder upload button */}
        <div className="mt-3 flex gap-2">
          <button
            onClick={(e) => { e.stopPropagation(); folderInputRef.current?.click(); }}
            className="rounded-lg border border-neutral-200 px-3 py-1.5 text-sm text-neutral-500 transition-colors hover:border-purple-400 hover:text-purple-500 dark:border-neutral-700 dark:text-neutral-400"
          >
            Upload folder
          </button>
          {selectedFiles.length > 0 && (
            <button
              onClick={clearFiles}
              className="rounded-lg border border-neutral-200 px-3 py-1.5 text-sm text-neutral-400 transition-colors hover:border-red-300 hover:text-red-500 dark:border-neutral-700"
            >
              Clear all
            </button>
          )}
        </div>

        {/* Selected files list */}
        {selectedFiles.length > 0 && (
          <div className="mt-4 space-y-3">
            <p className="text-sm font-medium text-neutral-600 dark:text-neutral-300">
              {selectedFiles.length} file(s) selected
            </p>
            <div className="max-h-48 space-y-1 overflow-y-auto">
              {selectedFiles.map((file, idx) => (
                <div key={`${file.name}-${idx}`} className="flex items-center justify-between rounded-lg bg-neutral-50 px-3 py-1.5 text-sm dark:bg-neutral-800">
                  <span className="truncate">{file.name}</span>
                  <div className="ml-2 flex items-center gap-2">
                    <span className="text-xs text-neutral-400">{formatSize(file.size)}</span>
                    <button
                      onClick={() => removeFile(idx)}
                      className="text-neutral-400 hover:text-red-500"
                    >
                      x
                    </button>
                  </div>
                </div>
              ))}
            </div>

            {/* Target format + Model + convert button */}
            <div className="flex items-center gap-4 flex-wrap">
              <div>
                <label className="mb-1 block text-sm text-neutral-500">{t.converter.targetFormat}</label>
                <div className="flex gap-2">
                  {allTargets.map((fmt) => (
                    <button
                      key={fmt}
                      onClick={() => setTargetFormat(fmt)}
                      className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                        targetFormat === fmt
                          ? "border border-purple-500 bg-purple-50 text-purple-700 dark:bg-purple-950/30 dark:text-purple-300"
                          : "border border-neutral-200 bg-white text-neutral-600 hover:border-neutral-300 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-400"
                      }`}
                    >
                      {FORMAT_LABELS[fmt]}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="mb-1 block text-sm text-neutral-500">Model</label>
                <div className="flex gap-2">
                  <button
                    onClick={() => setSelectedModel("claude-opus-4-6")}
                    className={`rounded-lg px-3 py-2 text-sm transition-colors ${
                      selectedModel === "claude-opus-4-6"
                        ? "border border-purple-500 bg-purple-50 text-purple-700 dark:bg-purple-950/30 dark:text-purple-300"
                        : "border border-neutral-200 bg-white text-neutral-600 hover:border-neutral-300 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-400"
                    }`}
                  >
                    <span className="font-medium">Opus 4.7</span>
                  </button>
                  <button
                    onClick={() => setSelectedModel("claude-sonnet-4-6")}
                    className={`rounded-lg px-3 py-2 text-sm transition-colors ${
                      selectedModel === "claude-sonnet-4-6"
                        ? "border border-purple-500 bg-purple-50 text-purple-700 dark:bg-purple-950/30 dark:text-purple-300"
                        : "border border-neutral-200 bg-white text-neutral-600 hover:border-neutral-300 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-400"
                    }`}
                  >
                    <span className="font-medium">Sonnet 4.6</span>
                    <span className="ml-1 text-xs text-neutral-400">40% cheaper</span>
                  </button>
                  <button
                    onClick={() => setSelectedModel("claude-haiku-4-5")}
                    className={`rounded-lg px-3 py-2 text-sm transition-colors ${
                      selectedModel === "claude-haiku-4-5"
                        ? "border border-purple-500 bg-purple-50 text-purple-700 dark:bg-purple-950/30 dark:text-purple-300"
                        : "border border-neutral-200 bg-white text-neutral-600 hover:border-neutral-300 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-400"
                    }`}
                  >
                    <span className="font-medium">Haiku 4.5</span>
                    <span className="ml-1 text-xs text-neutral-400">80% cheaper</span>
                  </button>
                  <button
                    onClick={() => setSelectedModel("kimi-k2.6")}
                    className={`rounded-lg px-3 py-2 text-sm transition-colors ${
                      selectedModel === "kimi-k2.6"
                        ? "border border-purple-500 bg-purple-50 text-purple-700 dark:bg-purple-950/30 dark:text-purple-300"
                        : "border border-neutral-200 bg-white text-neutral-600 hover:border-neutral-300 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-400"
                    }`}
                  >
                    <span className="font-medium">Kimi K2.6</span>
                    <span className="ml-1 text-xs text-neutral-400">95% cheaper</span>
                  </button>
                </div>
                <p className="mt-1.5 text-xs text-neutral-400">
                  {selectedModel === "claude-opus-4-6" && "Best quality. Complex tables, merged cells, multi-column layouts. ~$2.50/page."}
                  {selectedModel === "claude-sonnet-4-6" && "Good quality for most documents. Tables and formatting preserved. ~$1.50/page."}
                  {selectedModel === "claude-haiku-4-5" && "Fast and affordable. Works well for text-heavy documents with simple tables. ~$0.50/page."}
                  {selectedModel === "kimi-k2.6" && (
                    <span className="text-amber-500">Experimental. Simple text only — complex tables or forms may produce corrupted files. ~$0.10/page.</span>
                  )}
                </p>
              </div>
              <button
                onClick={() => void handleBulkUpload()}
                disabled={uploading || !targetFormat || !allTargets.includes(targetFormat)}
                className="rounded-lg bg-purple-600 px-6 py-2 font-medium text-white transition-colors hover:bg-purple-700 disabled:opacity-50 self-end"
              >
                {uploading ? uploadProgress || "..." : `${t.converter.startConversion} (${selectedFiles.length})`}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Job History */}
      <div>
        <h2 className="mb-4 text-lg font-semibold">{t.converter.history}</h2>
        {loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : jobs.length === 0 ? (
          <p className="text-sm text-neutral-400">{t.converter.noJobs}</p>
        ) : (
          <div className="space-y-2">
            {jobs.map((job) => (
              <div
                key={job.id}
                className="flex items-center justify-between rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{job.source_filename}</span>
                    <span className="text-neutral-300 dark:text-neutral-600">→</span>
                    <span className="font-medium text-purple-600 dark:text-purple-400">
                      {FORMAT_LABELS[job.target_format] ?? job.target_format.toUpperCase()}
                    </span>
                    <span className="text-xs text-neutral-400">{formatTime(job.created_at)}</span>
                  </div>

                  {!["completed", "failed"].includes(job.status) && (
                    <div className="mt-2">
                      <p className="text-sm text-neutral-500">
                        {job.progress_message ?? statusLabel(job.status, t)}
                      </p>
                      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-700">
                        <div
                          className="h-full rounded-full bg-purple-500 transition-all duration-500"
                          style={{ width: `${job.progress_pct}%` }}
                        />
                      </div>
                    </div>
                  )}
                  {job.status === "failed" && (
                    <p className="mt-1 text-sm text-red-500">
                      {job.error_message ?? t.converter.statusFailed}
                    </p>
                  )}
                  {job.status === "completed" && (
                    <p className="mt-1 text-sm text-green-600">
                      {t.converter.statusCompleted}
                      {job.credits_actual != null && job.credits_actual > 0 && (
                        <span className="ml-2 text-neutral-400">
                          (${(job.credits_actual / 100).toFixed(2)})
                        </span>
                      )}
                    </p>
                  )}
                </div>

                <div className="ml-4 flex items-center gap-2">
                  {job.status === "completed" && (
                    <button
                      onClick={() => void handleDownload(job.id)}
                      className="rounded-lg border border-green-200 bg-green-50 px-3 py-1.5 text-sm text-green-700 transition-colors hover:bg-green-100 dark:border-green-800 dark:bg-green-950/30 dark:text-green-400 dark:hover:bg-green-950/50"
                    >
                      {t.converter.download}
                    </button>
                  )}
                  {job.status === "failed" && job.result_filename && (
                    <button
                      onClick={() => void handleDownload(job.id)}
                      className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-sm text-amber-700 transition-colors hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-400"
                    >
                      Partial
                    </button>
                  )}
                  {["completed", "failed"].includes(job.status) && (
                    <button
                      onClick={() => void handleDelete(job.id)}
                      className="rounded-lg border border-neutral-200 px-3 py-1.5 text-sm text-neutral-500 transition-colors hover:border-red-300 hover:text-red-500 dark:border-neutral-700 dark:text-neutral-400 dark:hover:border-red-700 dark:hover:text-red-400"
                    >
                      {t.converter.delete}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
