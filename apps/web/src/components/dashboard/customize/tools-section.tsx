"use client";

import { useState, useEffect, useCallback } from "react";

interface ToolStats {
  calls: number;
  errors: number;
  avgDurationMs: number;
  lastCallAt: number;
}

interface ToolMetadata {
  name: string;
  description: string;
  permission: string;
  kind: string;
  enabled: boolean;
  source: "builtin" | "skill" | "external";
  isConcurrencySafe: boolean;
  dangerous: boolean;
  tags: string[];
  stats: ToolStats;
}

interface ToolsSectionProps {
  botId: string;
  disabled?: boolean;
}

const SOURCE_LABEL: Record<string, string> = {
  builtin: "시스템",
  skill: "스킬",
  external: "커스텀",
};

const SOURCE_BADGE: Record<string, string> = {
  builtin: "bg-gray-100 text-gray-500",
  skill: "bg-purple-50 text-purple-600",
  external: "bg-blue-50 text-blue-600",
};

const PERM_LABEL: Record<string, string> = {
  read: "읽기",
  write: "쓰기",
  execute: "실행",
  net: "네트워크",
  meta: "메타",
};

export function ToolsSection({ botId, disabled = false }: ToolsSectionProps): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const [tools, setTools] = useState<ToolMetadata[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState(false);
  const [filter, setFilter] = useState<"all" | "builtin" | "skill" | "external">("all");
  const [search, setSearch] = useState("");
  const [expandedTool, setExpandedTool] = useState<string | null>(null);

  const fetchTools = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/bots/${botId}/tools`);
      if (res.ok) {
        const data: { tools?: ToolMetadata[] } = await res.json();
        setTools(data.tools ?? []);
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, [botId]);

  useEffect(() => {
    if (expanded && !fetched) void fetchTools();
  }, [expanded, fetched, fetchTools]);

  const handleToggle = async (toolName: string, currentEnabled: boolean): Promise<void> => {
    const action = currentEnabled ? "disable" : "enable";
    await fetch(`/api/bots/${botId}/tools/${encodeURIComponent(toolName)}/${action}`, {
      method: "PUT",
    });
    setTools((prev) =>
      prev.map((t) => (t.name === toolName ? { ...t, enabled: !currentEnabled } : t)),
    );
  };

  const handleDelete = async (toolName: string): Promise<void> => {
    const res = await fetch(`/api/bots/${botId}/tools/${encodeURIComponent(toolName)}`, {
      method: "DELETE",
    });
    if (res.ok) setTools((prev) => prev.filter((t) => t.name !== toolName));
  };

  const filtered = tools.filter((t) => {
    if (filter !== "all" && t.source !== filter) return false;
    if (search && !t.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const enabledCount = tools.filter((t) => t.enabled).length;

  return (
    <div className="rounded-2xl border border-black/[0.06] bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-50/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-purple-50 flex items-center justify-center">
            <span className="text-sm">🔧</span>
          </div>
          <div className="text-left">
            <p className="text-sm font-semibold text-foreground">도구 관리</p>
            <p className="text-xs text-secondary">봇이 사용할 수 있는 도구</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {fetched && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-purple-50 text-purple-600 font-medium">
              {enabledCount}/{tools.length}
            </span>
          )}
          <svg
            className={`w-4 h-4 text-secondary transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>

      {expanded && (
        <div className="border-t border-black/[0.04] px-5 py-4 space-y-3">
          {/* Search + filter */}
          <div className="flex gap-2 items-center">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="도구 검색..."
              className="flex-1 max-w-xs rounded-xl border border-black/[0.08] bg-white px-3 py-2 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-primary/30"
            />
            <div className="flex gap-1">
              {(["all", "builtin", "skill", "external"] as const).map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setFilter(f)}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                    filter === f
                      ? "bg-primary text-white"
                      : "bg-gray-100 text-secondary hover:bg-gray-200"
                  }`}
                >
                  {f === "all" ? "전체" : SOURCE_LABEL[f]}
                </button>
              ))}
            </div>
          </div>

          {loading ? (
            <p className="text-sm text-secondary py-6 text-center">불러오는 중...</p>
          ) : (
            <div className="space-y-1.5">
              {filtered.map((tool) => (
                <div
                  key={tool.name}
                  className="rounded-xl border border-black/[0.06] bg-white overflow-hidden"
                >
                  <div
                    className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50/50 transition-colors"
                    onClick={() => setExpandedTool(expandedTool === tool.name ? null : tool.name)}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${SOURCE_BADGE[tool.source]}`}>
                          {SOURCE_LABEL[tool.source]}
                        </span>
                        <span className="text-sm font-medium truncate">{tool.name}</span>
                        {tool.dangerous && <span className="text-[10px] text-red-500">위험</span>}
                      </div>
                      <p className="text-xs text-secondary truncate mt-0.5">{tool.description}</p>
                    </div>
                    <span className="text-xs text-secondary">{PERM_LABEL[tool.permission] ?? tool.permission}</span>
                    <span className="text-xs text-secondary tabular-nums w-14 text-right">{tool.stats.calls.toLocaleString()}회</span>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); void handleToggle(tool.name, tool.enabled); }}
                      disabled={disabled}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                        tool.enabled ? "bg-primary" : "bg-gray-200"
                      } disabled:opacity-50`}
                    >
                      <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${tool.enabled ? "translate-x-4" : "translate-x-0.5"}`} />
                    </button>
                  </div>

                  {expandedTool === tool.name && (
                    <div className="border-t border-black/[0.04] px-4 py-3 text-xs space-y-2 bg-gray-50/30">
                      <div className="grid grid-cols-4 gap-3">
                        <div><div className="text-secondary">호출</div><div className="font-medium tabular-nums">{tool.stats.calls.toLocaleString()}</div></div>
                        <div><div className="text-secondary">에러</div><div className="font-medium tabular-nums">{tool.stats.errors}</div></div>
                        <div><div className="text-secondary">평균 시간</div><div className="font-medium tabular-nums">{tool.stats.avgDurationMs}ms</div></div>
                        <div><div className="text-secondary">동시실행</div><div className="font-medium">{tool.isConcurrencySafe ? "가능" : "불가"}</div></div>
                      </div>
                      {tool.tags.length > 0 && (
                        <div className="flex gap-1 pt-1">
                          {tool.tags.map((tag) => (
                            <span key={tag} className="text-xs px-1.5 py-0.5 rounded-full bg-gray-100 text-secondary">{tag}</span>
                          ))}
                        </div>
                      )}
                      <div className="flex items-center justify-between pt-1">
                        <div className="text-secondary">종류: {tool.kind} · 권한: {tool.permission}</div>
                        {tool.source !== "builtin" && (
                          <button
                            type="button"
                            onClick={() => void handleDelete(tool.name)}
                            disabled={disabled}
                            className="text-red-400 hover:text-red-500 text-xs transition-colors disabled:opacity-40"
                          >
                            삭제
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {fetched && !loading && filtered.length === 0 && (
            <div className="text-center py-8 text-secondary text-xs">검색 결과가 없습니다</div>
          )}
        </div>
      )}
    </div>
  );
}
