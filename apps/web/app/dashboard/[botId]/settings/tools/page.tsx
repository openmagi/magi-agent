"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";

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

export default function ToolsSettingsPage() {
  const params = useParams();
  const botId = params.botId as string;
  const [tools, setTools] = useState<ToolMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "builtin" | "skill" | "external">("all");
  const [search, setSearch] = useState("");
  const [expandedTool, setExpandedTool] = useState<string | null>(null);

  const fetchTools = useCallback(async () => {
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
    }
  }, [botId]);

  useEffect(() => {
    void fetchTools();
  }, [fetchTools]);

  const handleToggle = async (toolName: string, currentEnabled: boolean) => {
    const action = currentEnabled ? "disable" : "enable";
    await fetch(`/api/bots/${botId}/tools/${encodeURIComponent(toolName)}/${action}`, {
      method: "PUT",
    });
    setTools((prev) =>
      prev.map((t) => (t.name === toolName ? { ...t, enabled: !currentEnabled } : t)),
    );
  };

  const handleDelete = async (toolName: string) => {
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

  const permLabel: Record<string, string> = {
    read: "\uC77D\uAE30",
    write: "\uC4F0\uAE30",
    execute: "\uC2E4\uD589",
    net: "\uB124\uD2B8\uC6CC\uD06C",
    meta: "\uBA54\uD0C0",
  };
  const sourceLabel: Record<string, string> = {
    builtin: "\uC2DC\uC2A4\uD15C",
    skill: "\uC2A4\uD0AC",
    external: "\uCEE4\uC2A4\uD140",
  };
  const sourceBadgeColor: Record<string, string> = {
    builtin: "bg-zinc-100 dark:bg-zinc-800 text-zinc-500",
    skill: "bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400",
    external: "bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400",
  };

  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <h2 className="text-xl font-semibold mb-1">
          {"\uB3C4\uAD6C \uAD00\uB9AC (Tools)"}
        </h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          {"\uBD07\uC774 \uC0AC\uC6A9\uD560 \uC218 \uC788\uB294 \uB3C4\uAD6C\uB97C \uAD00\uB9AC\uD569\uB2C8\uB2E4"}
        </p>
      </div>

      <div className="flex gap-3 items-center">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={"\uB3C4\uAD6C \uAC80\uC0C9..."}
          className="flex-1 max-w-xs rounded-lg border border-zinc-300 dark:border-zinc-600 bg-transparent px-4 py-2 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <div className="flex gap-1">
          {(["all", "builtin", "skill", "external"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                filter === f
                  ? "bg-blue-600 text-white"
                  : "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-700"
              }`}
            >
              {f === "all" ? "\uC804\uCCB4" : sourceLabel[f]}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-zinc-400">
          {"\uBD88\uB7EC\uC624\uB294 \uC911..."}
        </p>
      ) : (
        <div className="space-y-2">
          {filtered.map((tool) => (
            <div
              key={tool.name}
              className="rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 overflow-hidden"
            >
              <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors"
                onClick={() =>
                  setExpandedTool(expandedTool === tool.name ? null : tool.name)
                }
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-xs px-1.5 py-0.5 rounded ${sourceBadgeColor[tool.source]}`}
                    >
                      {sourceLabel[tool.source]}
                    </span>
                    <span className="text-sm font-medium truncate">{tool.name}</span>
                    {tool.dangerous && (
                      <span className="text-xs text-red-500">
                        {"\uC704\uD5D8"}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-zinc-400 truncate mt-0.5">
                    {tool.description}
                  </p>
                </div>
                <span className="text-xs text-zinc-400">
                  {permLabel[tool.permission] ?? tool.permission}
                </span>
                <span className="text-xs text-zinc-400 tabular-nums w-16 text-right">
                  {tool.stats.calls.toLocaleString()}{" "}
                  {"\uD68C"}
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleToggle(tool.name, tool.enabled);
                  }}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                    tool.enabled
                      ? "bg-blue-600"
                      : "bg-zinc-300 dark:bg-zinc-600"
                  }`}
                >
                  <span
                    className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
                      tool.enabled ? "translate-x-4" : "translate-x-0.5"
                    }`}
                  />
                </button>
              </div>

              {expandedTool === tool.name && (
                <div className="border-t border-zinc-100 dark:border-zinc-800 px-4 py-3 text-xs space-y-2 bg-zinc-50/50 dark:bg-zinc-800/30">
                  <div className="grid grid-cols-4 gap-3">
                    <div>
                      <div className="text-zinc-400">
                        {"\uD638\uCD9C"}
                      </div>
                      <div className="font-medium tabular-nums">
                        {tool.stats.calls.toLocaleString()}
                      </div>
                    </div>
                    <div>
                      <div className="text-zinc-400">
                        {"\uC5D0\uB7EC"}
                      </div>
                      <div className="font-medium tabular-nums">
                        {tool.stats.errors}
                      </div>
                    </div>
                    <div>
                      <div className="text-zinc-400">
                        {"\uD3C9\uADE0 \uC2DC\uAC04"}
                      </div>
                      <div className="font-medium tabular-nums">
                        {tool.stats.avgDurationMs}ms
                      </div>
                    </div>
                    <div>
                      <div className="text-zinc-400">
                        {"\uB3D9\uC2DC\uC2E4\uD589"}
                      </div>
                      <div className="font-medium">
                        {tool.isConcurrencySafe
                          ? "\uAC00\uB2A5"
                          : "\uBD88\uAC00"}
                      </div>
                    </div>
                  </div>
                  {tool.tags.length > 0 && (
                    <div className="flex gap-1 pt-1">
                      {tool.tags.map((tag) => (
                        <span
                          key={tag}
                          className="text-xs px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center justify-between pt-1">
                    <div className="text-zinc-400">
                      {"\uC885\uB958"}: {tool.kind} · {"\uAD8C\uD55C"}:{" "}
                      {tool.permission}
                    </div>
                    {tool.source !== "builtin" && (
                      <button
                        onClick={() => void handleDelete(tool.name)}
                        className="text-red-500 hover:text-red-600 text-xs transition-colors"
                      >
                        {"\uC0AD\uC81C"}
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {!loading && filtered.length === 0 && (
        <div className="text-center py-16 text-zinc-400 text-sm">
          {"\uAC80\uC0C9 \uACB0\uACFC\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4"}
        </div>
      )}
    </div>
  );
}
