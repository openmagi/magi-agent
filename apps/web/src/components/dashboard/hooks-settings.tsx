import { useState, useEffect, useCallback } from "react";

interface HookStats {
  totalRuns: number;
  timeouts: number;
  errors: number;
  blocks: number;
  avgDurationMs: number;
  lastRunAt: number;
}

interface HookInfo {
  name: string;
  point: string;
  priority: number;
  blocking: boolean;
  enabled: boolean;
  source: "builtin" | "custom" | "runtime";
  failOpen: boolean;
  stats: HookStats;
}

interface GeneratedHookConfig {
  name: string;
  point: string;
  priority: number;
  blocking: boolean;
  failOpen: boolean;
  description: string;
  checkLogic: string;
}

export interface HooksSettingsProps {
  getJson: (path: string) => Promise<Record<string, unknown>>;
  sendJson: (path: string, body: Record<string, unknown>) => Promise<Record<string, unknown>>;
  deleteJson: (path: string, body: Record<string, unknown>) => Promise<Record<string, unknown>>;
}

const POINT_LABELS: Record<string, string> = {
  beforeTurnStart: "Before Turn Start",
  afterTurnEnd: "After Turn End",
  beforeLLMCall: "Before LLM Call",
  afterLLMCall: "After LLM Call",
  beforeToolUse: "Before Tool Use",
  afterToolUse: "After Tool Use",
  beforeCommit: "Before Commit",
  afterCommit: "After Commit",
  onAbort: "On Abort",
  onError: "On Error",
  onTaskCheckpoint: "Task Checkpoint",
  beforeCompaction: "Before Compaction",
  afterCompaction: "After Compaction",
  onRuleViolation: "Rule Violation",
  onArtifactCreated: "Artifact Created",
};

function HookCard({
  hook,
  expanded,
  onToggleExpand,
  onToggle,
  onDelete,
  canDelete,
}: {
  hook: HookInfo;
  expanded: boolean;
  onToggleExpand: () => void;
  onToggle: () => void;
  onDelete?: () => void;
  canDelete?: boolean;
}) {
  const blockRate =
    hook.stats.totalRuns > 0
      ? ((hook.stats.blocks / hook.stats.totalRuns) * 100).toFixed(1)
      : "0.0";

  return (
    <div className="overflow-hidden rounded-xl border border-gray-100 bg-white">
      <div
        className="flex cursor-pointer items-center gap-3 px-4 py-3 transition-colors hover:bg-gray-50"
        onClick={onToggleExpand}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-secondary">
              {hook.source === "builtin" ? "SYS" : "USR"}
            </span>
            <span className="truncate text-sm font-medium text-foreground">
              {hook.name.replace("builtin:", "").replace("custom:", "")}
            </span>
          </div>
        </div>
        <span className="whitespace-nowrap text-xs text-secondary">
          {POINT_LABELS[hook.point] ?? hook.point}
        </span>
        <span className="w-12 text-right text-xs tabular-nums text-secondary">{blockRate}%</span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            hook.enabled ? "bg-primary" : "bg-gray-300"
          }`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
              hook.enabled ? "translate-x-4" : "translate-x-0.5"
            }`}
          />
        </button>
      </div>

      {expanded && (
        <div className="space-y-2 border-t border-gray-100 bg-gray-50/50 px-4 py-3 text-xs">
          <div className="grid grid-cols-4 gap-3">
            <div>
              <div className="text-secondary">Runs</div>
              <div className="font-medium tabular-nums">{hook.stats.totalRuns.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-secondary">Blocks</div>
              <div className="font-medium tabular-nums">{hook.stats.blocks.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-secondary">Errors</div>
              <div className="font-medium tabular-nums">{hook.stats.errors}</div>
            </div>
            <div>
              <div className="text-secondary">Timeouts</div>
              <div className="font-medium tabular-nums">{hook.stats.timeouts}</div>
            </div>
          </div>
          <div className="flex items-center justify-between pt-1">
            <div className="text-secondary">
              Priority {hook.priority} / {hook.blocking ? "Blocking" : "Observer"} /{" "}
              {hook.failOpen ? "Fail-open" : "Fail-closed"}
            </div>
            {canDelete && onDelete && (
              <button
                onClick={onDelete}
                className="text-xs text-red-500 transition-colors hover:text-red-600"
              >
                Remove
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function HooksSettings({ getJson, sendJson, deleteJson }: HooksSettingsProps) {
  const [hooks, setHooks] = useState<HookInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [nlInput, setNlInput] = useState("");
  const [converting, setConverting] = useState(false);
  const [preview, setPreview] = useState<GeneratedHookConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedHook, setExpandedHook] = useState<string | null>(null);

  const fetchHooks = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getJson("/v1/hooks");
      const list = Array.isArray(data.hooks) ? (data.hooks as HookInfo[]) : [];
      setHooks(list);
    } catch {
      /* runtime may not be connected */
    } finally {
      setLoading(false);
    }
  }, [getJson]);

  useEffect(() => {
    void fetchHooks();
  }, [fetchHooks]);

  const handleConvert = async (): Promise<void> => {
    if (!nlInput.trim()) return;
    setConverting(true);
    setError(null);
    setPreview(null);
    try {
      const data = await sendJson("/api/hooks/from-natural-language", {
        description: nlInput,
      });
      if (data.name) {
        setPreview(data as unknown as GeneratedHookConfig);
      } else {
        setError(typeof data.error === "string" ? data.error : "Conversion failed");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Server error");
    } finally {
      setConverting(false);
    }
  };

  const handleToggle = async (hookName: string, currentEnabled: boolean): Promise<void> => {
    const action = currentEnabled ? "disable" : "enable";
    try {
      await sendJson(`/v1/hooks/${encodeURIComponent(hookName)}/${action}`, {});
      setHooks((prev) =>
        prev.map((h) => (h.name === hookName ? { ...h, enabled: !currentEnabled } : h)),
      );
    } catch {
      /* ignore */
    }
  };

  const handleDelete = async (hookName: string): Promise<void> => {
    try {
      await deleteJson(`/v1/hooks/${encodeURIComponent(hookName)}`, {});
      setHooks((prev) => prev.filter((h) => h.name !== hookName));
    } catch {
      /* ignore */
    }
  };

  const builtinHooks = hooks.filter((h) => h.source === "builtin");
  const customHooks = hooks.filter((h) => h.source !== "builtin");

  return (
    <div className="space-y-5">
      {/* Natural language hook creation */}
      <div className="rounded-xl border border-gray-100 bg-white px-5 py-4">
        <p className="mb-1 text-sm font-semibold text-foreground">Create a hook from natural language</p>
        <p className="mb-3 text-xs leading-5 text-secondary">
          Describe the rule you want enforced. Magi will generate a typed hook configuration.
        </p>
        <div className="flex gap-3">
          <input
            type="text"
            value={nlInput}
            onChange={(e) => setNlInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !converting) void handleConvert();
            }}
            placeholder='e.g. "Block responses containing investment advice without disclaimers"'
            className="flex-1 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-primary"
          />
          <button
            onClick={() => void handleConvert()}
            disabled={converting || !nlInput.trim()}
            className="rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {converting ? "Generating..." : "Generate Hook"}
          </button>
        </div>

        {error && <p className="mt-2 text-sm text-red-500">{error}</p>}

        {preview && (
          <div className="mt-3 space-y-3 rounded-xl border border-emerald-500/20 bg-emerald-500/10 p-4">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium text-emerald-700">Preview</p>
              <button
                onClick={() => {
                  setPreview(null);
                  setNlInput("");
                }}
                className="rounded-lg bg-emerald-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-emerald-700"
              >
                Add Hook
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <span className="text-secondary">Name:</span> {preview.name}
              </div>
              <div>
                <span className="text-secondary">Point:</span>{" "}
                {POINT_LABELS[preview.point] ?? preview.point}
              </div>
              <div>
                <span className="text-secondary">Priority:</span> {preview.priority}
              </div>
              <div>
                <span className="text-secondary">Blocking:</span>{" "}
                {preview.blocking ? "Yes" : "No"}
              </div>
            </div>
            {preview.description && (
              <p className="text-xs text-secondary">{preview.description}</p>
            )}
            {preview.checkLogic && (
              <p className="text-xs italic text-secondary">{preview.checkLogic}</p>
            )}
          </div>
        )}
      </div>

      {/* Custom hooks */}
      {customHooks.length > 0 && (
        <div className="space-y-3">
          <p className="text-sm font-semibold text-foreground">Custom Hooks</p>
          <div className="space-y-2">
            {customHooks.map((hook) => (
              <HookCard
                key={hook.name}
                hook={hook}
                expanded={expandedHook === hook.name}
                onToggleExpand={() =>
                  setExpandedHook(expandedHook === hook.name ? null : hook.name)
                }
                onToggle={() => void handleToggle(hook.name, hook.enabled)}
                onDelete={() => void handleDelete(hook.name)}
                canDelete
              />
            ))}
          </div>
        </div>
      )}

      {/* Builtin hooks */}
      <div className="space-y-3">
        <p className="text-sm font-semibold text-foreground">
          System Hooks
          <span className="ml-2 text-xs font-normal text-secondary">{builtinHooks.length}</span>
        </p>
        {loading ? (
          <p className="py-8 text-center text-sm text-secondary">Loading hooks...</p>
        ) : builtinHooks.length === 0 ? (
          <p className="py-8 text-center text-sm text-secondary">No hooks registered.</p>
        ) : (
          <div className="space-y-2">
            {builtinHooks.map((hook) => (
              <HookCard
                key={hook.name}
                hook={hook}
                expanded={expandedHook === hook.name}
                onToggleExpand={() =>
                  setExpandedHook(expandedHook === hook.name ? null : hook.name)
                }
                onToggle={() => void handleToggle(hook.name, hook.enabled)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
