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

export interface ToolsSettingsProps {
  getJson: (path: string) => Promise<Record<string, unknown>>;
  putJson: (path: string, body: Record<string, unknown>) => Promise<Record<string, unknown>>;
  deleteJson: (path: string, body: Record<string, unknown>) => Promise<Record<string, unknown>>;
}

const PERM_LABELS: Record<string, string> = {
  none: "None",
  "fs:read": "Read",
  "fs:write": "Write",
  net: "Network",
  exec: "Shell",
  spawn: "Spawn",
};

const SOURCE_LABELS: Record<string, string> = {
  builtin: "System",
  skill: "Skill",
  external: "Custom",
};

const SOURCE_BADGE: Record<string, string> = {
  builtin: "bg-gray-100 text-gray-500",
  skill: "bg-purple-100 text-purple-600",
  external: "bg-blue-100 text-blue-600",
};

export function ToolsSettings({ getJson, putJson, deleteJson }: ToolsSettingsProps) {
  const [tools, setTools] = useState<ToolMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "builtin" | "skill" | "external">("all");
  const [search, setSearch] = useState("");
  const [expandedTool, setExpandedTool] = useState<string | null>(null);

  const fetchTools = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getJson("/v1/admin/tools");
      const list = Array.isArray(data.tools) ? (data.tools as ToolMetadata[]) : [];
      setTools(list);
    } catch {
      /* runtime may not be connected */
    } finally {
      setLoading(false);
    }
  }, [getJson]);

  useEffect(() => {
    void fetchTools();
  }, [fetchTools]);

  const handleToggle = async (toolName: string, currentEnabled: boolean) => {
    const action = currentEnabled ? "disable" : "enable";
    try {
      await putJson(`/v1/admin/tools/${encodeURIComponent(toolName)}/${action}`, {});
      setTools((prev) =>
        prev.map((t) => (t.name === toolName ? { ...t, enabled: !currentEnabled } : t)),
      );
    } catch {
      /* ignore */
    }
  };

  const handleDelete = async (toolName: string) => {
    try {
      await deleteJson(`/v1/admin/tools/${encodeURIComponent(toolName)}`, {});
      setTools((prev) => prev.filter((t) => t.name !== toolName));
    } catch {
      /* ignore */
    }
  };

  const filtered = tools.filter((t) => {
    if (filter !== "all" && t.source !== filter) return false;
    if (search && !t.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const counts = {
    all: tools.length,
    builtin: tools.filter((t) => t.source === "builtin").length,
    skill: tools.filter((t) => t.source === "skill").length,
    external: tools.filter((t) => t.source === "external").length,
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search tools..."
          className="max-w-xs flex-1 rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-primary"
        />
        <div className="flex gap-1">
          {(["all", "builtin", "skill", "external"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                filter === f
                  ? "bg-primary text-white"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200"
              }`}
            >
              {f === "all" ? "All" : SOURCE_LABELS[f]} ({counts[f]})
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <p className="py-8 text-center text-sm text-secondary">Loading tools...</p>
      ) : filtered.length === 0 ? (
        <p className="py-12 text-center text-sm text-secondary">
          {search ? "No tools match the search." : "No tools registered."}
        </p>
      ) : (
        <div className="space-y-2">
          {filtered.map((tool) => (
            <div
              key={tool.name}
              className="overflow-hidden rounded-xl border border-gray-100 bg-white"
            >
              <div
                className="flex cursor-pointer items-center gap-3 px-4 py-3 transition-colors hover:bg-gray-50"
                onClick={() => setExpandedTool(expandedTool === tool.name ? null : tool.name)}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className={`rounded px-1.5 py-0.5 text-xs ${SOURCE_BADGE[tool.source]}`}>
                      {SOURCE_LABELS[tool.source]}
                    </span>
                    <span className="truncate text-sm font-medium text-foreground">{tool.name}</span>
                    {tool.dangerous && (
                      <span className="text-xs text-red-500">Dangerous</span>
                    )}
                  </div>
                  <p className="mt-0.5 truncate text-xs text-secondary">{tool.description}</p>
                </div>
                <span className="whitespace-nowrap text-xs text-secondary">
                  {PERM_LABELS[tool.permission] ?? tool.permission}
                </span>
                <span className="w-16 text-right text-xs tabular-nums text-secondary">
                  {tool.stats.calls.toLocaleString()} calls
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleToggle(tool.name, tool.enabled);
                  }}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                    tool.enabled ? "bg-primary" : "bg-gray-300"
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
                <div className="space-y-2 border-t border-gray-100 bg-gray-50/50 px-4 py-3 text-xs">
                  <div className="grid grid-cols-4 gap-3">
                    <div>
                      <div className="text-secondary">Calls</div>
                      <div className="font-medium tabular-nums">{tool.stats.calls.toLocaleString()}</div>
                    </div>
                    <div>
                      <div className="text-secondary">Errors</div>
                      <div className="font-medium tabular-nums">{tool.stats.errors}</div>
                    </div>
                    <div>
                      <div className="text-secondary">Avg Duration</div>
                      <div className="font-medium tabular-nums">{tool.stats.avgDurationMs}ms</div>
                    </div>
                    <div>
                      <div className="text-secondary">Concurrent</div>
                      <div className="font-medium">{tool.isConcurrencySafe ? "Yes" : "No"}</div>
                    </div>
                  </div>
                  {tool.tags.length > 0 && (
                    <div className="flex gap-1 pt-1">
                      {tool.tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-secondary"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center justify-between pt-1">
                    <div className="text-secondary">
                      Kind: {tool.kind} / Permission: {tool.permission}
                    </div>
                    {tool.source !== "builtin" && (
                      <button
                        onClick={() => void handleDelete(tool.name)}
                        className="text-xs text-red-500 transition-colors hover:text-red-600"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
