"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/lib/i18n";

function t(locale: string, en: string, ko: string): string {
  return locale === "ko" ? ko : en;
}

interface MemoryEditorProps {
  botId: string;
  botName: string;
  botOnline: boolean;
}

interface MemoryFile {
  name: string;
  path: string;
  tier: "root" | "daily" | "weekly" | "monthly" | "system";
}

interface SearchResult {
  path: string;
  score: number;
  snippet: string;
}

type SearchMode = "filename" | "content";

const TIERS = [
  { key: "root" as const, label: "Root", dir: null, files: ["memory/ROOT.md"] },
  { key: "system" as const, label: "System", dir: null, files: ["SCRATCHPAD.md", "WORKING.md"] },
  { key: "daily" as const, label: "Daily", dir: "memory/daily" },
  { key: "weekly" as const, label: "Weekly", dir: "memory/weekly" },
  { key: "monthly" as const, label: "Monthly", dir: "memory/monthly" },
];

async function apiFetch(_botId: string, params: Record<string, string>): Promise<Record<string, unknown>> {
  const { agentFetch } = await import("@/lib/local-api");
  const qs = new URLSearchParams(params).toString();
  const res = await agentFetch(`/v1/memory?${qs}`);
  return res.json();
}

async function apiPost(_botId: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const { agentFetch } = await import("@/lib/local-api");
  const res = await agentFetch("/v1/memory", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

export function MemoryEditor({ botId, botName, botOnline }: MemoryEditorProps) {
  const { locale } = useI18n();
  const [tree, setTree] = useState<Record<string, MemoryFile[]>>({});
  const [expandedTiers, setExpandedTiers] = useState<Set<string>>(new Set(["root", "system", "daily"]));
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>("");
  const [editContent, setEditContent] = useState<string>("");
  const [editMode, setEditMode] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [searchMode, setSearchMode] = useState<SearchMode>("filename");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const loadTree = useCallback(async () => {
    setLoading(true);
    const result: Record<string, MemoryFile[]> = {};

    // Load root + system files individually
    for (const tier of TIERS) {
      if (tier.files) {
        result[tier.key] = tier.files.map(f => ({
          name: f.split("/").pop()!,
          path: f,
          tier: tier.key,
        }));
      }
    }

    // Load directory-based tiers in parallel
    const dirTiers = TIERS.filter(t => t.dir);
    const responses = await Promise.all(
      dirTiers.map(t => apiFetch(botId, { action: "list", dir: t.dir! }))
    );

    dirTiers.forEach((tier, i) => {
      const files = (responses[i].files as string[]) || [];
      result[tier.key] = files
        .filter(f => f.endsWith(".md"))
        .sort((a, b) => b.localeCompare(a))
        .map(f => ({
          name: f,
          path: `${tier.dir}/${f}`,
          tier: tier.key,
        }));
    });

    // Also load raw daily files (memory/YYYY-MM-DD.md)
    const rawRes = await apiFetch(botId, { action: "list", dir: "memory" });
    const rawFiles = ((rawRes.files as string[]) || [])
      .filter(f => /^\d{4}-\d{2}-\d{2}\.md$/.test(f))
      .sort((a, b) => b.localeCompare(a));

    result.daily = [
      ...rawFiles.map(f => ({ name: f, path: `memory/${f}`, tier: "daily" as const })),
      ...(result.daily || []),
    ];

    setTree(result);
    setLoading(false);
  }, [botId]);

  useEffect(() => { loadTree(); }, [loadTree]);

  const loadFile = useCallback(async (path: string) => {
    const data = await apiFetch(botId, { action: "read", path });
    const content = (data.content as string) ?? "";
    setFileContent(content);
    setEditContent(content);
    setSelectedFile(path);
    setEditMode(false);
    setDirty(false);
  }, [botId]);

  const handleSave = useCallback(async () => {
    if (!selectedFile || !dirty) return;
    setSaving(true);
    await apiPost(botId, { action: "write", path: selectedFile, content: editContent });
    setFileContent(editContent);
    setDirty(false);
    setSaving(false);
  }, [botId, selectedFile, editContent, dirty]);

  const handleDelete = useCallback(async (paths: string[]) => {
    if (paths.length === 0) return;
    const label = paths.length === 1
      ? paths[0]
      : t(locale, `${paths.length} files`, `${paths.length}개 파일`);
    const confirmMsg = t(
      locale,
      `Delete "${label}"? This action cannot be undone.`,
      `"${label}"을(를) 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`,
    );
    if (!window.confirm(confirmMsg)) return;
    await apiPost(botId, { action: "delete", paths });
    if (selectedFile && paths.includes(selectedFile)) {
      setSelectedFile(null);
      setFileContent("");
      setEditContent("");
    }
    setSelectedFiles(new Set());
    loadTree();
  }, [botId, selectedFile, loadTree, locale]);

  const handleBulkClearDaily = useCallback(async () => {
    const dailyFiles = [...(tree.daily || [])];
    if (dailyFiles.length === 0) return;
    const confirmMsg = t(
      locale,
      `Delete all ${dailyFiles.length} daily logs? This action cannot be undone.`,
      `${dailyFiles.length}개의 일일 로그를 모두 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`,
    );
    if (!window.confirm(confirmMsg)) return;
    await apiPost(botId, { action: "delete", paths: dailyFiles.map(f => f.path) });
    if (selectedFile && dailyFiles.some(f => f.path === selectedFile)) {
      setSelectedFile(null);
      setFileContent("");
      setEditContent("");
    }
    loadTree();
  }, [botId, tree, selectedFile, loadTree, locale]);

  const handleResetTree = useCallback(async () => {
    const allCompaction = [
      ...(tree.daily || []),
      ...(tree.weekly || []),
      ...(tree.monthly || []),
    ];
    if (allCompaction.length === 0) return;
    const confirmMsg = t(
      locale,
      `Reset the entire compaction tree (${allCompaction.length} files)? ROOT.md will be kept. This action cannot be undone.`,
      `컴팩션 트리 전체(${allCompaction.length}개 파일)를 초기화하시겠습니까? ROOT.md는 유지됩니다. 이 작업은 되돌릴 수 없습니다.`,
    );
    if (!window.confirm(confirmMsg)) return;
    await apiPost(botId, { action: "delete", paths: allCompaction.map(f => f.path) });
    setSelectedFile(null);
    setFileContent("");
    setEditContent("");
    setSelectedFiles(new Set());
    loadTree();
  }, [botId, tree, loadTree, locale]);

  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) {
      setSearchResults(null);
      return;
    }
    if (searchMode === "filename") {
      const q = searchQuery.toLowerCase();
      const matches: MemoryFile[] = [];
      Object.values(tree).forEach(files => {
        files.forEach(f => {
          if (f.name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q)) {
            matches.push(f);
          }
        });
      });
      setSearchResults(matches.map(f => ({ path: f.path, score: 1, snippet: f.name })));
      return;
    }
    // QMD content search
    setSearching(true);
    const data = await apiFetch(botId, { action: "search", query: searchQuery, limit: "15", minScore: "0.1" });
    setSearchResults((data.results as SearchResult[]) || []);
    setSearching(false);
  }, [botId, searchQuery, searchMode, tree]);

  const toggleTier = (key: string) => {
    setExpandedTiers(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleSelect = (path: string) => {
    setSelectedFiles(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const totalFiles = Object.values(tree).reduce((sum, files) => sum + files.length, 0);

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Memory</h1>
          <p className="text-sm text-secondary mt-1">
            {botName} — {t(locale, `${totalFiles} files`, `${totalFiles}개 파일`)}
            {!botOnline && <span className="ml-2 text-amber-600">({t(locale, "Bot offline", "봇 오프라인")})</span>}
          </p>
        </div>
      </div>

      {/* Search + Bulk Actions */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <div className="flex-1 flex gap-2">
          <div className="flex-1">
            <Input
              placeholder={searchMode === "filename" ? t(locale, "Search filenames...", "파일명 검색...") : t(locale, "Search memory content (QMD)...", "메모리 내용 검색 (QMD)...")}
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleSearch()}
              disabled={searchMode === "content" && !botOnline}
            />
          </div>
          <Button variant="secondary" size="sm" onClick={() => {
            setSearchMode(prev => prev === "filename" ? "content" : "filename");
            setSearchResults(null);
          }}>
            {searchMode === "filename" ? t(locale, "Filename", "파일명") : t(locale, "Content", "내용")}
          </Button>
          <Button variant="primary" size="sm" onClick={handleSearch} disabled={searching || (searchMode === "content" && !botOnline)}>
            {searching ? t(locale, "Searching...", "검색 중...") : t(locale, "Search", "검색")}
          </Button>
        </div>
        <div className="flex gap-2">
          {selectedFiles.size > 0 && (
            <Button variant="ghost" size="sm" className="text-red-600 hover:text-red-700 hover:bg-red-50" onClick={() => handleDelete(Array.from(selectedFiles))}>
              {t(locale, "Delete selected", "선택 삭제")} ({selectedFiles.size})
            </Button>
          )}
          <Button variant="ghost" size="sm" className="text-red-600 hover:text-red-700 hover:bg-red-50" onClick={handleBulkClearDaily}>
            {t(locale, "Delete all daily logs", "일일 로그 전체 삭제")}
          </Button>
          <Button variant="ghost" size="sm" className="text-red-600 hover:text-red-700 hover:bg-red-50" onClick={handleResetTree}>
            {t(locale, "Reset tree", "트리 초기화")}
          </Button>
        </div>
      </div>

      {/* Search Results */}
      {searchResults && (
        <GlassCard className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-foreground">{t(locale, "Search results", "검색 결과")} ({searchResults.length})</h3>
            <button className="text-xs text-secondary hover:text-foreground" onClick={() => setSearchResults(null)}>{t(locale, "Close", "닫기")}</button>
          </div>
          {searchResults.length === 0 ? (
            <p className="text-sm text-secondary">{t(locale, "No results", "결과 없음")}</p>
          ) : (
            <div className="space-y-1 max-h-60 overflow-y-auto">
              {searchResults.map(r => (
                <button
                  key={r.path}
                  onClick={() => loadFile(r.path)}
                  className={`w-full text-left px-3 py-2 rounded-lg text-sm hover:bg-gray-100 transition-colors ${
                    selectedFile === r.path ? "bg-primary/10 text-primary-light" : "text-foreground"
                  }`}
                >
                  <span className="font-medium">{r.path}</span>
                  {r.score < 1 && <span className="ml-2 text-xs text-secondary">({(r.score * 100).toFixed(0)}%)</span>}
                  {r.snippet && <p className="text-xs text-secondary mt-0.5 truncate">{r.snippet}</p>}
                </button>
              ))}
            </div>
          )}
        </GlassCard>
      )}

      <div className="flex gap-6 min-h-[600px]">
        {/* Left: Tree Explorer */}
        <GlassCard className="w-72 shrink-0 overflow-y-auto max-h-[75vh] !p-4">
          {loading ? (
            <p className="text-sm text-secondary animate-pulse">{t(locale, "Loading...", "로딩 중...")}</p>
          ) : (
            <div className="space-y-1">
              {TIERS.map(tier => {
                const files = tree[tier.key] || [];
                const expanded = expandedTiers.has(tier.key);
                return (
                  <div key={tier.key}>
                    <button
                      onClick={() => toggleTier(tier.key)}
                      className="w-full flex items-center justify-between px-2 py-1.5 rounded-lg text-sm font-semibold hover:bg-gray-100 transition-colors"
                    >
                      <span className="flex items-center gap-1.5">
                        <svg className={`w-3.5 h-3.5 text-gray-400 transition-transform ${expanded ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                        </svg>
                        {tier.label}
                      </span>
                      <span className="text-xs text-secondary bg-gray-100 px-1.5 py-0.5 rounded-full">{files.length}</span>
                    </button>
                    {expanded && files.length > 0 && (
                      <div className="ml-3 mt-0.5 space-y-0.5">
                        {files.map(file => (
                          <div key={file.path} className="flex items-center group">
                            <input
                              type="checkbox"
                              className="mr-1.5 opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer accent-primary"
                              checked={selectedFiles.has(file.path)}
                              onChange={() => toggleSelect(file.path)}
                            />
                            <button
                              onClick={() => loadFile(file.path)}
                              className={`flex-1 text-left px-2 py-1 rounded-md text-xs truncate transition-colors ${
                                selectedFile === file.path
                                  ? "bg-primary/10 text-primary-light font-medium"
                                  : "text-gray-600 hover:bg-gray-50 hover:text-foreground"
                              }`}
                              title={file.path}
                            >
                              {file.name}
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </GlassCard>

        {/* Right: File Viewer/Editor */}
        <GlassCard className="flex-1 flex flex-col !p-0 overflow-hidden">
          {!selectedFile ? (
            <div className="flex-1 flex items-center justify-center text-secondary text-sm">
              {t(locale, "Select a file from the tree on the left", "왼쪽 트리에서 파일을 선택하세요")}
            </div>
          ) : (
            <>
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-foreground truncate">{selectedFile}</p>
                </div>
                <div className="flex items-center gap-2 ml-4">
                  <Button
                    variant={editMode ? "primary" : "secondary"}
                    size="sm"
                    onClick={() => {
                      if (editMode && dirty) {
                        if (!window.confirm(t(locale, "You have unsaved changes. Switch to preview?", "저장하지 않은 변경사항이 있습니다. 미리보기로 전환하시겠습니까?"))) return;
                        setEditContent(fileContent);
                        setDirty(false);
                      }
                      setEditMode(!editMode);
                    }}
                    disabled={!botOnline}
                  >
                    {editMode ? t(locale, "Preview", "미리보기") : t(locale, "Edit", "편집")}
                  </Button>
                  {editMode && (
                    <Button variant="primary" size="sm" onClick={handleSave} disabled={!dirty || saving}>
                      {saving ? t(locale, "Saving...", "저장 중...") : t(locale, "Save", "저장")}
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-red-600 hover:text-red-700 hover:bg-red-50"
                    onClick={() => handleDelete([selectedFile])}
                    disabled={!botOnline}
                  >
                    {t(locale, "Delete", "삭제")}
                  </Button>
                </div>
              </div>

              {/* Content */}
              <div className="flex-1 overflow-y-auto p-5">
                {editMode ? (
                  <textarea
                    ref={textareaRef}
                    value={editContent}
                    onChange={e => { setEditContent(e.target.value); setDirty(true); }}
                    className="w-full h-full min-h-[500px] font-mono text-sm text-foreground bg-transparent border-none outline-none resize-none leading-relaxed"
                    spellCheck={false}
                  />
                ) : (
                  <pre className="text-sm text-foreground whitespace-pre-wrap break-words font-mono leading-relaxed">
                    {fileContent || <span className="text-secondary italic">{t(locale, "File is empty", "파일이 비어있습니다")}</span>}
                  </pre>
                )}
              </div>

              {/* Footer status */}
              {dirty && (
                <div className="px-5 py-2 border-t border-gray-200 text-xs text-amber-600">
                  {t(locale, "You have unsaved changes", "저장하지 않은 변경사항이 있습니다")}
                </div>
              )}
            </>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
