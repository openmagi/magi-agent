"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { WorkConsolePanel } from "./work-console-panel";
import { MissionsPanel, type MissionChannelType, type MissionFocusRequest } from "./missions-panel";
import {
  OPEN_MISSION_LEDGER_EVENT,
  readOpenMissionLedgerEvent,
} from "@/lib/chat/mission-ledger-events";
import type {
  ChannelState,
  ChatResponseLanguage,
  ControlRequestRecord,
  KbDocReference,
  QueuedMessage,
} from "@/lib/chat/types";
import type { KbCollectionWithDocs, KbDocEntry } from "@/hooks/use-kb-docs";
import {
  buildKbPreviewUrl,
  getDefaultKbPanelScope,
  getKbPanelHiddenRowCount,
  getKbPanelDocumentRows,
  getKbScopeBuckets,
  type KbPanelScope,
} from "@/lib/knowledge/kb-panel-scope";
import {
  KB_UPLOAD_ACCEPT,
  KB_UPLOAD_EXTENSIONS,
  resolveKnowledgeUploadMimeType,
} from "@/lib/knowledge/upload-mime";
import {
  buildWorkspaceFileTree,
  buildWorkspaceFileContentUrl,
  formatWorkspaceFileSize,
  type WorkspaceFileEntry,
  type WorkspaceFilePreviewKind,
  type WorkspaceFileTreeNode,
} from "@/lib/workspace/workspace-files";

const PANEL_KEY = "clawy:kbPanelExpanded";
const PANEL_WIDTH_KEY = "clawy:kbPanelWidth";
const PREVIEW_HEIGHT_KEY = "clawy:kbPreviewHeight";
const PANEL_SCOPE_KEY = "clawy:kbPanelScope";
const PANEL_VIEW_KEY = "clawy:rightInspectorView";
const PANEL_VIEW_V2_KEY = "clawy:rightInspectorView:v2";
const MIN_PANEL_WIDTH = 200;
const MAX_PANEL_WIDTH = 600;
const DEFAULT_PANEL_WIDTH = 320;
const SIDEBAR_WIDTH = 256;
const MIN_CENTER_WIDTH = 300;
const MIN_PREVIEW_HEIGHT = 80;
const KB_PANEL_ROW_LIMIT = 20;

type PanelScope = KbPanelScope | "workspace";
export type RightInspectorView = "work" | "missions" | "knowledge";

interface KbSidePanelProps {
  botId: string;
  collections: KbCollectionWithDocs[];
  loading: boolean;
  refreshing: boolean;
  workspaceFiles: WorkspaceFileEntry[];
  workspaceLoading: boolean;
  workspaceRefreshing: boolean;
  selectedDocs: KbDocReference[];
  onToggleDoc: (doc: KbDocReference) => void;
  onRefresh: () => void;
  onWorkspaceRefresh: () => void;
  getAccessToken?: () => Promise<string | null>;
  missionChannelType?: MissionChannelType;
  missionChannelId?: string | null;
  channelState?: ChannelState;
  queuedMessages?: QueuedMessage[];
  controlRequests?: ControlRequestRecord[];
  onViewChange?: (view: RightInspectorView) => void;
  onWorkOpenChange?: (open: boolean) => void;
  uiLanguage?: ChatResponseLanguage;
}

const EMPTY_CHANNEL_STATE: ChannelState = {
  streaming: false,
  streamingText: "",
  thinkingText: "",
  error: null,
  hasTextContent: false,
  thinkingStartedAt: null,
  turnPhase: null,
  heartbeatElapsedMs: null,
  pendingInjectionCount: 0,
  activeTools: [],
  subagents: [],
  taskBoard: null,
  missions: [],
  activeGoalMissionId: null,
  pendingGoalMissionTitle: null,
  fileProcessing: false,
};

function hasOpenTaskState(channelState: ChannelState): boolean {
  return !!channelState.taskBoard?.tasks.some(
    (task) => task.status === "pending" || task.status === "in_progress",
  );
}

function shouldSuppressInlineRunDetails(
  channelState: ChannelState,
  queuedMessages: QueuedMessage[],
  controlRequests: ControlRequestRecord[],
): boolean {
  const pendingRequests = controlRequests.filter((request) => request.state === "pending");
  const hasLiveWork =
    (channelState.activeTools ?? []).length > 0 ||
    (channelState.subagents ?? []).some(
      (subagent) => subagent.status === "running" || subagent.status === "waiting",
    ) ||
    hasOpenTaskState(channelState) ||
    !!channelState.browserFrame ||
    queuedMessages.length > 0 ||
    pendingRequests.length > 0 ||
    channelState.fileProcessing ||
    channelState.reconnecting;

  return hasLiveWork || (channelState.streaming && !channelState.streamingText);
}

function parseRightInspectorView(value: string | null): RightInspectorView | null {
  if (value === "work" || value === "missions" || value === "knowledge") return value;
  return null;
}

function getInitialRightInspectorView(): RightInspectorView {
  if (typeof window === "undefined") return "knowledge";
  try {
    const v2 = parseRightInspectorView(localStorage.getItem(PANEL_VIEW_V2_KEY));
    if (v2) return v2;

    const legacy = parseRightInspectorView(localStorage.getItem(PANEL_VIEW_KEY));
    return legacy === "missions" || legacy === "knowledge" ? legacy : "knowledge";
  } catch {
    return "knowledge";
  }
}

interface PreviewState {
  id: string;
  source: "kb" | "workspace";
  filename: string;
  content: string | null;
  loading: boolean;
  error: string | null;
  previewKind?: WorkspaceFilePreviewKind;
  url?: string;
  downloadUrl?: string;
}

function FolderIcon(): React.ReactElement {
  return (
    <svg className="w-3.5 h-3.5 text-secondary/45" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
    </svg>
  );
}

function FileIcon(): React.ReactElement {
  return (
    <svg className="w-3.5 h-3.5 text-secondary/35" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M14.25 2.25H6.75A2.25 2.25 0 004.5 4.5v15a2.25 2.25 0 002.25 2.25h10.5a2.25 2.25 0 002.25-2.25V7.5l-5.25-5.25z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M14.25 2.25V7.5h5.25" />
    </svg>
  );
}

function DownloadIcon(): React.ReactElement {
  return (
    <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v12m0 0l4-4m-4 4l-4-4M5 21h14" />
    </svg>
  );
}

function WorkspaceFileTreeRows({
  nodes,
  botId,
  previewId,
  onPreviewFile,
}: {
  nodes: WorkspaceFileTreeNode[];
  botId: string;
  previewId: string | null;
  onPreviewFile: (file: WorkspaceFileEntry) => void;
}): React.ReactElement {
  return (
    <>
      {nodes.map((node) => {
        if (node.type === "directory") {
          return (
            <div key={`dir:${node.path}`} className="mb-0.5" role="treeitem" aria-expanded="true" aria-selected="false">
              <div className="flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-foreground/75">
                <FolderIcon />
                <span className="min-w-0 flex-1 truncate text-[11px] font-medium">
                  {node.name}
                </span>
                <span className="text-[10px] text-secondary/35">
                  {node.fileCount}
                </span>
              </div>
              {node.children.length > 0 && (
                <div className="ml-3 border-l border-black/[0.05] pl-2" role="group">
                  <WorkspaceFileTreeRows
                    nodes={node.children}
                    botId={botId}
                    previewId={previewId}
                    onPreviewFile={onPreviewFile}
                  />
                </div>
              )}
            </div>
          );
        }

        const file = node.file;
        const currentPreviewId = `workspace:${file.path}`;
        const isPreviewing = previewId === currentPreviewId;
        const downloadUrl = buildWorkspaceFileContentUrl({ botId, path: file.path, mode: "download" });
        return (
          <div
            key={`file:${file.path}`}
            className={`flex items-center gap-1 rounded-md transition-colors ${
              isPreviewing ? "bg-black/[0.05]" : "hover:bg-black/[0.03]"
            }`}
            role="treeitem"
            aria-selected={isPreviewing}
          >
            <span className="ml-1 shrink-0">
              <FileIcon />
            </span>
            <button
              type="button"
              onClick={() => onPreviewFile(file)}
              className="min-w-0 flex-1 py-1 pl-1 pr-1 text-left text-foreground/70 cursor-pointer"
              title={file.path}
            >
              <span className="block truncate text-[11px]">
                {file.filename}
              </span>
              <span className="block truncate text-[9px] text-secondary/35">
                {file.path} · {formatWorkspaceFileSize(file.size)}
              </span>
            </button>
            <a
              href={downloadUrl}
              className="shrink-0 rounded-md p-1.5 text-secondary/40 hover:text-foreground hover:bg-black/[0.04] transition-colors"
              aria-label={`Download ${file.filename}`}
              title={`Download ${file.filename}`}
            >
              <DownloadIcon />
            </a>
          </div>
        );
      })}
    </>
  );
}

/** Collapsible Knowledge Base file-tree panel on the right side of chat. */
export function KbSidePanel({
  botId,
  collections,
  loading,
  refreshing,
  workspaceFiles,
  workspaceLoading,
  workspaceRefreshing,
  selectedDocs,
  onToggleDoc,
  onRefresh,
  onWorkspaceRefresh,
  getAccessToken,
  missionChannelType,
  missionChannelId,
  channelState = EMPTY_CHANNEL_STATE,
  queuedMessages = [],
  controlRequests = [],
  onViewChange,
  onWorkOpenChange,
  uiLanguage,
}: KbSidePanelProps): React.ReactElement {
  const [expanded, setExpanded] = useState(() => {
    if (typeof window === "undefined") return true;
    try { return localStorage.getItem(PANEL_KEY) !== "0"; } catch { return true; }
  });
  const [panelWidth, setPanelWidth] = useState(() => {
    if (typeof window === "undefined") return DEFAULT_PANEL_WIDTH;
    try { const v = localStorage.getItem(PANEL_WIDTH_KEY); return v ? Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, parseInt(v, 10))) : DEFAULT_PANEL_WIDTH; } catch { return DEFAULT_PANEL_WIDTH; }
  });
  const [previewHeight, setPreviewHeight] = useState(() => {
    if (typeof window === "undefined") return 250;
    try { const v = localStorage.getItem(PREVIEW_HEIGHT_KEY); return v ? Math.max(MIN_PREVIEW_HEIGHT, parseInt(v, 10)) : 250; } catch { return 250; }
  });
  const [activeScope, setActiveScope] = useState<PanelScope>(() => {
    if (typeof window === "undefined") return getDefaultKbPanelScope(collections);
    try {
      const value = localStorage.getItem(PANEL_SCOPE_KEY);
      if (value === "workspace") return value;
      if (value === "org" || value === "personal") return value;
      return getDefaultKbPanelScope(collections);
    } catch {
      return getDefaultKbPanelScope(collections);
    }
  });
  const [activeView, setActiveView] =
    useState<RightInspectorView>(getInitialRightInspectorView);
  const [missionFocusRequest, setMissionFocusRequest] = useState<MissionFocusRequest | null>(null);
  const [search, setSearch] = useState("");
  const [openCols, setOpenCols] = useState<Set<string>>(() => new Set());
  const [expandedCollectionIds, setExpandedCollectionIds] = useState<Set<string>>(() => new Set());
  const [collapsedDocIds, setCollapsedDocIds] = useState<Set<string>>(() => new Set());
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadingCollectionId, setUploadingCollectionId] = useState<string | null>(null);
  const [pendingUploadCollection, setPendingUploadCollection] = useState<KbCollectionWithDocs | null>(null);
  const selectedIds = new Set(selectedDocs.map((d) => d.id));
  const panelRef = useRef<HTMLDivElement>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const missionFocusNonce = useRef(0);
  const isDraggingWidth = useRef(false);
  const isDraggingHeight = useRef(false);
  const scopeBuckets = useMemo(() => getKbScopeBuckets(collections), [collections]);
  const activeCollections = useMemo(() => {
    if (activeScope === "workspace") return [];
    return scopeBuckets[activeScope].collections;
  }, [activeScope, scopeBuckets]);
  const isWorkspaceScope = activeScope === "workspace";
  const panelRefreshing = isWorkspaceScope ? workspaceRefreshing : refreshing;
  const suppressInlineRunDetails = shouldSuppressInlineRunDetails(
    channelState,
    queuedMessages,
    controlRequests,
  );

  const selectView = useCallback((view: RightInspectorView) => {
    setActiveView(view);
    try {
      localStorage.setItem(PANEL_VIEW_KEY, view);
      localStorage.setItem(PANEL_VIEW_V2_KEY, view);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    const onOpenMissionLedger = (event: Event) => {
      const detail = readOpenMissionLedgerEvent(event);
      if (!detail) return;
      setExpanded(true);
      try { localStorage.setItem(PANEL_KEY, "1"); } catch { /* ignore */ }
      selectView("missions");
      missionFocusNonce.current += 1;
      setMissionFocusRequest({
        missionId: detail.missionId,
        nonce: missionFocusNonce.current,
      });
    };
    window.addEventListener(OPEN_MISSION_LEDGER_EVENT, onOpenMissionLedger);
    return () => window.removeEventListener(OPEN_MISSION_LEDGER_EVENT, onOpenMissionLedger);
  }, [selectView]);

  useEffect(() => {
    onViewChange?.(activeView);
    onWorkOpenChange?.(expanded && activeView === "work");
  }, [activeView, expanded, onViewChange, onWorkOpenChange]);

  // Auto-expand the first visible collection for the active scope.
  useEffect(() => {
    if (activeScope === "workspace") return;
    if (activeCollections.length > 0 && !activeCollections.some((col) => openCols.has(col.id))) {
      setOpenCols((prev) => new Set([...prev, activeCollections[0].id]));
    }
  }, [activeCollections, activeScope, openCols]);

  useEffect(() => {
    if (loading || activeScope === "workspace") return;
    if (activeScope === "personal" && activeCollections.length === 0 && scopeBuckets.org.collections.length > 0) {
      setActiveScope("org");
    }
    if (activeScope === "org" && activeCollections.length === 0 && scopeBuckets.personal.collections.length > 0) {
      setActiveScope("personal");
    }
  }, [activeCollections.length, activeScope, loading, scopeBuckets.org.collections.length, scopeBuckets.personal.collections.length]);

  useEffect(() => {
    if (!expanded) return;
    const clamp = () => {
      const maxAllowed = window.innerWidth - SIDEBAR_WIDTH - MIN_CENTER_WIDTH;
      if (maxAllowed < MIN_PANEL_WIDTH) {
        setExpanded(false);
        try { localStorage.setItem(PANEL_KEY, "0"); } catch { /* */ }
      } else {
        setPanelWidth((w) => {
          const clamped = Math.min(w, maxAllowed);
          if (clamped !== w) try { localStorage.setItem(PANEL_WIDTH_KEY, String(clamped)); } catch { /* */ }
          return clamped;
        });
      }
    };
    clamp();
    window.addEventListener("resize", clamp);
    return () => window.removeEventListener("resize", clamp);
  }, [expanded]);

  const togglePanel = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      try { localStorage.setItem(PANEL_KEY, next ? "1" : "0"); } catch { /* ignore */ }
      return next;
    });
  }, []);

  const selectScope = useCallback((scope: PanelScope) => {
    setActiveScope(scope);
    try { localStorage.setItem(PANEL_SCOPE_KEY, scope); } catch { /* ignore */ }
  }, []);

  const toggleCollection = useCallback((colId: string) => {
    setOpenCols((prev) => {
      const next = new Set(prev);
      if (next.has(colId)) next.delete(colId);
      else next.add(colId);
      return next;
    });
  }, []);

  const toggleCollectionExpansion = useCallback((colId: string) => {
    setExpandedCollectionIds((prev) => {
      const next = new Set(prev);
      if (next.has(colId)) next.delete(colId);
      else next.add(colId);
      return next;
    });
  }, []);

  const toggleDocumentCollapse = useCallback((docId: string) => {
    setCollapsedDocIds((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  }, []);

  const openPreview = useCallback(async (doc: KbDocEntry) => {
    if (preview?.id === doc.id && !preview.error) {
      setPreview(null);
      return;
    }
    setPreview({ id: doc.id, source: "kb", filename: doc.filename, content: null, loading: true, error: null });
    try {
      const res = await fetch(buildKbPreviewUrl({ botId, doc }));
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.error || `Failed (${res.status})`);
      }
      const data = await res.json();
      setPreview((prev) =>
        prev?.id === doc.id
          ? { ...prev, content: data.content ?? "", loading: false }
          : prev,
      );
    } catch (err) {
      setPreview((prev) =>
        prev?.id === doc.id
          ? { ...prev, error: (err as Error).message, loading: false }
          : prev,
      );
    }
  }, [botId, preview]);

  const openWorkspacePreview = useCallback(async (file: WorkspaceFileEntry) => {
    const id = `workspace:${file.path}`;
    if (preview?.id === id && !preview.error) {
      setPreview(null);
      return;
    }

    const downloadUrl = buildWorkspaceFileContentUrl({ botId, path: file.path, mode: "download" });
    const inlineUrl = buildWorkspaceFileContentUrl({ botId, path: file.path, mode: "inline" });

    if (file.previewKind === "image" || file.previewKind === "pdf") {
      setPreview({
        id,
        source: "workspace",
        filename: file.filename,
        content: null,
        loading: false,
        error: null,
        previewKind: file.previewKind,
        url: inlineUrl,
        downloadUrl,
      });
      return;
    }

    if (file.previewKind === "download") {
      setPreview({
        id,
        source: "workspace",
        filename: file.filename,
        content: null,
        loading: false,
        error: null,
        previewKind: file.previewKind,
        downloadUrl,
      });
      return;
    }

    setPreview({
      id,
      source: "workspace",
      filename: file.filename,
      content: null,
      loading: true,
      error: null,
      previewKind: file.previewKind,
      downloadUrl,
    });

    try {
      const res = await fetch(buildWorkspaceFileContentUrl({ botId, path: file.path, mode: "content" }));
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.error || `Failed (${res.status})`);
      }
      const data = await res.json();
      setPreview((prev) =>
        prev?.id === id
          ? { ...prev, content: data.content ?? "", loading: false }
          : prev,
      );
    } catch (err) {
      setPreview((prev) =>
        prev?.id === id
          ? { ...prev, error: (err as Error).message, loading: false }
          : prev,
      );
    }
  }, [botId, preview]);

  const closePreview = useCallback(() => setPreview(null), []);

  const uploadFilesToCollection = useCallback(async (collection: KbCollectionWithDocs, files: File[]) => {
    const supportedFiles = files.filter((file) => {
      const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
      return KB_UPLOAD_EXTENSIONS.has(extension);
    });
    const skippedCount = files.length - supportedFiles.length;

    setUploadStatus(null);
    setUploadError(null);

    if (supportedFiles.length === 0) {
      setUploadError("No supported files found. Supported: PDF, DOCX, XLSX, XLS, PPTX, HWP, HWPX, HTML, EPUB, CSV, TXT, MD, JSON, ZIP");
      return;
    }

    setUploadingCollectionId(collection.id);
    const isOrgScope = collection.scope === "org" && !!collection.orgId;
    const token = await getAccessToken?.().catch(() => null);
    const authHeaders: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
    const failures: string[] = [];
    let uploadedCount = 0;

    try {
      for (const [index, file] of supportedFiles.entries()) {
        const contentType = resolveKnowledgeUploadMimeType(file);

        try {
          setUploadStatus(`Uploading ${file.name} (${index + 1}/${supportedFiles.length})...`);

          const uploadUrlRes = await fetch("/api/knowledge/upload-url", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...authHeaders,
            },
            body: JSON.stringify({
              collection: collection.name,
              filename: file.name,
              content_type: contentType,
              botId,
              ...(isOrgScope ? { scope: "org", orgId: collection.orgId } : {}),
            }),
          });

          if (!uploadUrlRes.ok) {
            const body = await uploadUrlRes.json().catch(() => null);
            failures.push(`${file.name}: ${body?.error || `Failed to prepare upload (${uploadUrlRes.status})`}`);
            continue;
          }

          const { upload_url: uploadUrl, signedUrl, storage_path: storagePath } = await uploadUrlRes.json();
          const directUploadUrl = uploadUrl || signedUrl;
          if (!directUploadUrl || !storagePath) {
            failures.push(`${file.name}: Upload URL response was incomplete`);
            continue;
          }

          const putRes = await fetch(directUploadUrl, {
            method: "PUT",
            headers: {
              "Content-Type": contentType,
              "x-upsert": "false",
            },
            body: file,
          });

          if (!putRes.ok) {
            failures.push(`${file.name}: Storage upload failed`);
            continue;
          }

          setUploadStatus(`Processing ${file.name} (${index + 1}/${supportedFiles.length})...`);

          const ingestRes = await fetch("/api/knowledge/upload", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...authHeaders,
            },
            body: JSON.stringify({
              collection: collection.name,
              filename: file.name,
              mime_type: contentType,
              storage_path: storagePath,
              botId,
              ...(isOrgScope ? { scope: "org", orgId: collection.orgId } : {}),
            }),
          });

          if (!ingestRes.ok) {
            const body = await ingestRes.json().catch(() => null);
            failures.push(`${file.name}: ${body?.error || `Failed to process (${ingestRes.status})`}`);
            continue;
          }

          uploadedCount += 1;
        } catch (error) {
          failures.push(`${file.name}: ${error instanceof Error ? error.message : "Network error"}`);
        }
      }

      await Promise.resolve(onRefresh());

      if (uploadedCount > 0) {
        const parts = [`${uploadedCount} file(s) uploaded`];
        if (skippedCount > 0) parts.push(`${skippedCount} unsupported skipped`);
        if (failures.length > 0) parts.push(`${failures.length} failed`);
        setUploadStatus(parts.join(" · "));
      } else {
        setUploadStatus(null);
      }

      setUploadError(failures.length > 0 ? failures.slice(0, 2).join(" · ") : null);
    } finally {
      setPendingUploadCollection(null);
      setUploadingCollectionId(null);
    }
  }, [botId, getAccessToken, onRefresh]);

  const openUploadPicker = useCallback((collection: KbCollectionWithDocs) => {
    setPendingUploadCollection(collection);
    setUploadError(null);
    setUploadStatus(null);
    uploadInputRef.current?.click();
  }, []);

  const handleUploadInputChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    const targetCollection = pendingUploadCollection;
    event.target.value = "";
    if (!targetCollection || files.length === 0) return;
    void uploadFilesToCollection(targetCollection, files);
  }, [pendingUploadCollection, uploadFilesToCollection]);

  // Horizontal resize (panel width)
  const startWidthDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDraggingWidth.current = true;
    const startX = e.clientX;
    const startW = panelWidth;
    const onMove = (ev: MouseEvent) => {
      // Dragging left edge: moving left = wider panel
      const delta = startX - ev.clientX;
      const viewportMax = window.innerWidth - SIDEBAR_WIDTH - MIN_CENTER_WIDTH;
      const newW = Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, viewportMax, startW + delta));
      setPanelWidth(newW);
    };
    const onUp = () => {
      isDraggingWidth.current = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      setPanelWidth((w) => { try { localStorage.setItem(PANEL_WIDTH_KEY, String(w)); } catch { /* */ } return w; });
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [panelWidth]);

  // Vertical resize (preview / file-list split)
  const startHeightDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDraggingHeight.current = true;
    const startY = e.clientY;
    const startH = previewHeight;
    const panelEl = panelRef.current;
    const maxH = panelEl ? panelEl.clientHeight * 0.8 : 500;
    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientY - startY;
      const newH = Math.max(MIN_PREVIEW_HEIGHT, Math.min(maxH, startH + delta));
      setPreviewHeight(newH);
    };
    const onUp = () => {
      isDraggingHeight.current = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      setPreviewHeight((h) => { try { localStorage.setItem(PREVIEW_HEIGHT_KEY, String(h)); } catch { /* */ } return h; });
    };
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [previewHeight]);

  const searchLower = search.toLowerCase();
  const filteredWorkspaceFiles = useMemo(() => {
    if (!searchLower) return workspaceFiles;
    return workspaceFiles.filter((file) =>
      `${file.path} ${file.filename}`.toLowerCase().includes(searchLower),
    );
  }, [searchLower, workspaceFiles]);
  const workspaceFileTree = useMemo(
    () => buildWorkspaceFileTree(filteredWorkspaceFiles),
    [filteredWorkspaceFiles],
  );

  // Collapsed state — just the icon bar
  if (!expanded) {
    return (
      <div className="hidden md:flex flex-col items-center w-10 border-l border-black/[0.06] bg-black/[0.01] pt-3">
        <button
          onClick={togglePanel}
          className="p-2 rounded-lg text-secondary/50 hover:text-foreground hover:bg-black/[0.04] transition-all cursor-pointer"
          aria-label="Expand inspector panel"
          title="Inspector"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
          </svg>
        </button>
        {selectedDocs.length > 0 && (
          <span className="mt-1 w-5 h-5 rounded-full bg-primary text-white text-[10px] font-bold flex items-center justify-center">
            {selectedDocs.length}
          </span>
        )}
      </div>
    );
  }

  return (
    <div ref={panelRef} className="hidden md:flex flex-row relative min-h-0 shrink-0" style={{ width: panelWidth }}>
      <input
        ref={uploadInputRef}
        type="file"
        multiple
        accept={KB_UPLOAD_ACCEPT}
        className="hidden"
        onChange={handleUploadInputChange}
      />
      {/* Horizontal drag handle (left edge) */}
      <div
        onMouseDown={startWidthDrag}
        className="w-1 cursor-col-resize hover:bg-primary/20 active:bg-primary/30 transition-colors shrink-0 z-10"
        role="separator"
        aria-label="Resize panel width"
      />
      <div className="flex flex-col flex-1 min-w-0 min-h-0 border-l border-black/[0.06] bg-black/[0.01] overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-black/[0.06]">
        <span className="text-[11px] font-semibold text-secondary/70 uppercase tracking-wide">
          {activeView === "work"
            ? "Work"
            : activeView === "missions"
              ? "Missions"
              : "Knowledge Base"}
        </span>
        <div className="flex items-center gap-0.5">
          {/* Refresh button */}
          {activeView === "knowledge" && (
            <button
              onClick={isWorkspaceScope ? onWorkspaceRefresh : onRefresh}
              disabled={panelRefreshing}
              className="p-1 rounded-md text-secondary/40 hover:text-foreground hover:bg-black/[0.04] transition-all cursor-pointer disabled:opacity-40"
              aria-label={isWorkspaceScope ? "Refresh workspace files" : "Refresh KB documents"}
              title="Refresh"
            >
              <svg
                className={`w-3.5 h-3.5 ${panelRefreshing ? "animate-spin" : ""}`}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
          )}
          {/* Collapse button */}
          <button
            onClick={togglePanel}
            className="p-1 rounded-md text-secondary/40 hover:text-foreground hover:bg-black/[0.04] transition-all cursor-pointer"
            aria-label="Collapse panel"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" />
            </svg>
          </button>
        </div>
      </div>

      <div className="px-2 pt-2">
        <div className="grid grid-cols-3 rounded-lg bg-black/[0.04] p-0.5" role="tablist" aria-label="Right inspector">
          {([
            ["work", "Work"],
            ["missions", "Missions"],
            ["knowledge", "Knowledge"],
          ] as const).map(([view, label]) => {
            const isActive = activeView === view;
            return (
              <button
                key={view}
                type="button"
                onClick={() => selectView(view)}
                className={`min-w-0 rounded-md px-2 py-1.5 text-[11px] font-medium transition-colors cursor-pointer ${
                  isActive
                    ? "bg-white text-foreground shadow-sm"
                    : "text-secondary/60 hover:text-foreground"
                }`}
                role="tab"
                aria-selected={isActive}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {activeView === "work" && (
        <div className="flex min-h-0 flex-1 flex-col">
          <WorkConsolePanel
            channelState={channelState}
            queuedMessages={queuedMessages}
            controlRequests={controlRequests}
            suppressInlineRunDetails={suppressInlineRunDetails}
            uiLanguage={uiLanguage}
          />
        </div>
      )}

      <div
        className={`${activeView === "missions" ? "flex" : "hidden"} min-h-0 flex-1 flex-col`}
        data-mission-channel-type={missionChannelType}
        data-mission-channel-id={missionChannelId ?? undefined}
      >
        <MissionsPanel
          botId={botId}
          channelType={missionChannelType}
          channelId={missionChannelId}
          liveMissions={channelState.missions ?? []}
          activeGoalMissionId={channelState.activeGoalMissionId ?? null}
          missionRefreshSeq={channelState.missionRefreshSeq ?? 0}
          lastMissionEventMissionId={channelState.lastMissionEventMissionId ?? null}
          focusMissionRequest={missionFocusRequest}
          pendingGoalTitle={channelState.pendingGoalMissionTitle ?? null}
          getAccessToken={getAccessToken}
        />
      </div>

      {activeView === "knowledge" && (
      <div className="flex min-h-0 flex-1 flex-col">

      {/* Scope tabs */}
      <div className="px-2 pt-2">
        <div className="grid grid-cols-3 rounded-lg bg-black/[0.04] p-0.5" role="tablist" aria-label="Knowledge Base scope">
          {([
            ["personal", "Personal", scopeBuckets.personal.documentCount],
            ["org", "Org", scopeBuckets.org.documentCount],
            ["workspace", "Workspace", workspaceFiles.length],
          ] as const).map(([scope, label, count]) => {
            const isActive = activeScope === scope;
            return (
              <button
                key={scope}
                type="button"
                onClick={() => selectScope(scope)}
                className={`min-w-0 rounded-md px-2 py-1.5 text-[11px] font-medium transition-colors cursor-pointer ${
                  isActive
                    ? "bg-white text-foreground shadow-sm"
                    : "text-secondary/60 hover:text-foreground"
                }`}
                role="tab"
                aria-selected={isActive}
              >
                <span className="inline-flex max-w-full items-center gap-1">
                  <span className="truncate">{label}</span>
                  <span className={isActive ? "text-secondary/60" : "text-secondary/40"}>
                    {count}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Preview pane */}
      {preview && (
        <>
        <div className="flex flex-col shrink-0" style={{ height: previewHeight }}>
          <div className="flex items-center justify-between px-3 py-1.5 bg-black/[0.02] border-b border-black/[0.06] shrink-0">
            <span className="text-[10px] font-medium text-foreground/70 truncate flex-1 mr-2">
              {preview.filename}
            </span>
            <button
              onClick={closePreview}
              className="p-0.5 rounded text-secondary/40 hover:text-foreground hover:bg-black/[0.04] transition-all cursor-pointer shrink-0"
              aria-label="Close preview"
            >
              <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-3 py-2 min-h-0">
            {preview.loading ? (
              <div className="flex items-center justify-center py-4">
                <svg className="w-4 h-4 text-secondary/30 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
              </div>
            ) : preview.error ? (
              <p className="text-[10px] text-red-500">{preview.error}</p>
            ) : preview.source === "workspace" && preview.previewKind === "image" && preview.url ? (
              <div className="relative h-full min-h-[120px] w-full">
                <Image
                  src={preview.url}
                  alt={preview.filename}
                  fill
                  sizes="320px"
                  unoptimized
                  className="rounded-md object-contain"
                />
              </div>
            ) : preview.source === "workspace" && preview.previewKind === "pdf" && preview.url ? (
              <iframe
                src={preview.url}
                title={preview.filename}
                className="h-full min-h-[180px] w-full rounded-md border border-black/[0.06] bg-white"
              />
            ) : preview.source === "workspace" && preview.previewKind === "download" ? (
              <div className="flex h-full min-h-[120px] flex-col items-center justify-center gap-2 text-center">
                <p className="text-[11px] text-secondary/60">
                  Preview is not available for this file type.
                </p>
                {preview.downloadUrl && (
                  <a
                    href={preview.downloadUrl}
                    className="rounded-md bg-black/[0.06] px-2.5 py-1.5 text-[11px] font-medium text-foreground/75 hover:bg-black/[0.1] transition-colors"
                  >
                    Download
                  </a>
                )}
              </div>
            ) : (
              <div className="prose-kb text-[12px] text-foreground/80 leading-relaxed break-words">
                <ReactMarkdown remarkPlugins={[[remarkGfm, { singleTilde: false }]]}>
                  {preview.content ?? ""}
                </ReactMarkdown>
              </div>
            )}
          </div>
        </div>
        {/* Vertical drag handle (between preview and file list) */}
        <div
          onMouseDown={startHeightDrag}
          className="h-1.5 cursor-row-resize hover:bg-primary/20 active:bg-primary/30 transition-colors shrink-0 flex items-center justify-center border-b border-black/[0.06]"
          role="separator"
          aria-label="Resize preview height"
        >
          <div className="w-8 h-0.5 rounded-full bg-black/[0.1]" />
        </div>
        </>
      )}

      {/* Search */}
      <div className="px-2 py-2">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search files..."
          className="w-full rounded-lg bg-black/[0.04] border border-black/[0.06] px-2.5 py-1.5 text-[11px] text-foreground placeholder-secondary/40 focus:outline-none focus:border-primary/30 transition-colors"
        />
      </div>

      {(uploadStatus || uploadError) && (
        <div className="px-2 pb-2 space-y-1">
          {uploadStatus && (
            <p className="rounded-lg bg-black/[0.03] px-2.5 py-1.5 text-[10px] text-foreground/65">
              {uploadStatus}
            </p>
          )}
          {uploadError && (
            <p className="rounded-lg bg-red-500/[0.06] px-2.5 py-1.5 text-[10px] text-red-500">
              {uploadError}
            </p>
          )}
        </div>
      )}

      {/* Collection tree */}
      <div className="flex-1 min-h-0 overflow-y-auto px-1 pb-2">
        {activeScope === "workspace" ? (
          workspaceLoading ? (
            <div className="flex items-center justify-center py-8">
              <svg className="w-4 h-4 text-secondary/30 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </div>
          ) : filteredWorkspaceFiles.length === 0 ? (
            <p className="text-[11px] text-secondary/40 text-center py-6 px-2">
              {searchLower ? "No matching generated files" : "No generated files yet"}
            </p>
          ) : (
            <div className="mb-0.5">
              <div className="flex items-center gap-1 pr-1">
                <div className="min-w-0 flex-1 flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-left">
                  <svg className="w-3.5 h-3.5 text-secondary/40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
                  </svg>
                  <span className="flex-1 text-[11px] font-medium text-foreground/80 truncate">
                    Generated files
                  </span>
                  <span className="text-[10px] text-secondary/40">
                    {filteredWorkspaceFiles.length}
                  </span>
                </div>
              </div>
              <div className="ml-3 pl-2 border-l border-black/[0.05]">
                <div role="tree" aria-label="Generated files">
                  <WorkspaceFileTreeRows
                    nodes={workspaceFileTree}
                    botId={botId}
                    previewId={preview?.id ?? null}
                    onPreviewFile={openWorkspacePreview}
                  />
                </div>
              </div>
            </div>
          )
        ) : loading ? (
          <div className="flex items-center justify-center py-8">
            <svg className="w-4 h-4 text-secondary/30 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </div>
        ) : activeCollections.length === 0 ? (
          <p className="text-[11px] text-secondary/40 text-center py-6 px-2">
            No {activeScope === "org" ? "org" : "personal"} collections yet
          </p>
        ) : (
          activeCollections.map((col) => {
            const isExpandedCollection = expandedCollectionIds.has(col.id);
            const rowLimit = searchLower || isExpandedCollection ? null : KB_PANEL_ROW_LIMIT;
            const documentRows = getKbPanelDocumentRows({
              docs: col.docs,
              collapsedIds: collapsedDocIds,
              search,
              limit: rowLimit,
            });
            const hiddenRowCount = searchLower
              ? 0
              : getKbPanelHiddenRowCount({
                docs: col.docs,
                collapsedIds: collapsedDocIds,
                search,
                limit: KB_PANEL_ROW_LIMIT,
              });
            if (searchLower && documentRows.length === 0) return null;
            const isOpen = openCols.has(col.id) || !!searchLower;

            return (
              <div key={col.id} className="mb-0.5">
                {/* Collection header */}
                <div className="flex items-center gap-1 pr-1">
                  <button
                    type="button"
                    onClick={() => toggleCollection(col.id)}
                    className="min-w-0 flex-1 flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-left hover:bg-black/[0.03] transition-colors cursor-pointer group"
                  >
                    <svg
                      className={`w-3 h-3 text-secondary/40 transition-transform ${isOpen ? "rotate-90" : ""}`}
                      viewBox="0 0 24 24"
                      fill="currentColor"
                    >
                      <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z" />
                    </svg>
                    <svg className="w-3.5 h-3.5 text-secondary/40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
                    </svg>
                    <span className="flex-1 text-[11px] font-medium text-foreground/80 truncate">
                      {col.name}
                    </span>
                    <span className="text-[10px] text-secondary/40">
                      {col.docs.length}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => openUploadPicker(col)}
                    disabled={uploadingCollectionId !== null}
                    className="shrink-0 rounded-md p-1.5 text-secondary/40 hover:text-foreground hover:bg-black/[0.04] transition-colors cursor-pointer disabled:cursor-wait disabled:opacity-50"
                    aria-label={`Upload files to ${col.name}`}
                    title={`Upload files to ${col.name}`}
                  >
                    {uploadingCollectionId === col.id ? (
                      <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                      </svg>
                    ) : (
                      <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14M5 12h14" />
                      </svg>
                    )}
                  </button>
                </div>

                {/* Documents */}
                {isOpen && (
                  <div className="ml-3 pl-2 border-l border-black/[0.05]">
                    {documentRows.map(({ doc, depth, hasChildren }) => {
                      const isSelected = selectedIds.has(doc.id);
                      const isReady = doc.status === "ready";
                      const isPreviewing = preview?.id === doc.id;
                      const isCollapsed = collapsedDocIds.has(doc.id);
                      return (
                        <div
                          key={doc.id}
                          className={`flex items-center gap-1 rounded-md transition-colors ${
                            isPreviewing
                              ? "bg-black/[0.05]"
                              : isSelected
                                ? "bg-primary/[0.08]"
                                : "hover:bg-black/[0.03]"
                          }`}
                          style={{ paddingLeft: Math.min(depth, 6) * 12 }}
                        >
                          {hasChildren ? (
                            <button
                              type="button"
                              onClick={() => toggleDocumentCollapse(doc.id)}
                              className="shrink-0 p-1 text-secondary/40 hover:text-foreground transition-colors cursor-pointer"
                              aria-label={`${isCollapsed ? "Expand" : "Collapse"} ${doc.filename}`}
                              title={isCollapsed ? "Expand" : "Collapse"}
                            >
                              <svg
                                className={`w-3 h-3 transition-transform ${isCollapsed ? "-rotate-90" : ""}`}
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth={2}
                              >
                                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                              </svg>
                            </button>
                          ) : (
                            <span className="w-5 shrink-0" />
                          )}
                          {/* Checkbox area — toggle selection */}
                          <button
                            type="button"
                            disabled={!isReady}
                            onClick={() =>
                              onToggleDoc({
                                id: doc.id,
                                filename: doc.filename,
                                collectionId: doc.collectionId,
                                collectionName: doc.collectionName,
                              })
                            }
                            className="shrink-0 p-1 pl-2 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
                            aria-label={`${isSelected ? "Deselect" : "Select"} ${doc.filename}`}
                          >
                            <span
                              className={`w-3.5 h-3.5 shrink-0 rounded border flex items-center justify-center transition-colors ${
                                isSelected
                                  ? "bg-primary border-primary"
                                  : "border-black/[0.15] bg-white"
                              }`}
                            >
                              {isSelected && (
                                <svg className="w-2.5 h-2.5 text-white" viewBox="0 0 20 20" fill="currentColor">
                                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                                </svg>
                              )}
                            </span>
                          </button>
                          {/* Filename area — click to preview */}
                          <button
                            type="button"
                            disabled={!isReady}
                            onClick={() => openPreview(doc)}
                            className={`flex-1 min-w-0 py-1 pr-2 text-left cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed ${
                              isSelected ? "text-primary-light" : "text-foreground/70"
                            }`}
                            title={doc.path ? `${doc.path}` : `Preview ${doc.filename}`}
                          >
                            <span className="block text-[11px] truncate">
                              {doc.filename}
                            </span>
                          </button>
                          {!isReady && (
                            <span className="text-[9px] text-amber-500 shrink-0 pr-2">
                              {doc.status}
                            </span>
                          )}
                        </div>
                      );
                    })}
                    {hiddenRowCount > 0 && !isExpandedCollection && (
                      <button
                        type="button"
                        onClick={() => toggleCollectionExpansion(col.id)}
                        className="px-2 py-1 text-[10px] text-secondary/50 hover:text-foreground transition-colors cursor-pointer"
                      >
                        +{hiddenRowCount} more
                      </button>
                    )}
                    {hiddenRowCount === 0 && isExpandedCollection && !searchLower && col.docs.length > KB_PANEL_ROW_LIMIT && (
                      <button
                        type="button"
                        onClick={() => toggleCollectionExpansion(col.id)}
                        className="px-2 py-1 text-[10px] text-secondary/40 hover:text-foreground transition-colors cursor-pointer"
                      >
                        Show less
                      </button>
                    )}
                    {documentRows.length === 0 && (
                      <p className="px-2 py-1 text-[10px] text-secondary/30">
                        {searchLower ? "No matching files" : "No files yet"}
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
      </div>
      )}
      </div>
    </div>
  );
}
