"use client";

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

interface HookConfig {
  name: string;
  point: string;
  priority: number;
  blocking: boolean;
  failOpen: boolean;
  description: string;
  checkLogic: string;
}

interface HooksSectionProps {
  botId: string;
  disabled?: boolean;
}

const POINT_LABEL: Record<string, string> = {
  beforeTurnStart: "턴 시작 전",
  beforeLLMCall: "LLM 호출 전",
  afterLLMCall: "LLM 호출 후",
  beforeToolUse: "도구 사용 전",
  afterToolUse: "도구 사용 후",
  beforeCommit: "응답 확정 전",
  afterCommit: "응답 확정 후",
  afterTurnEnd: "턴 종료 후",
};

export function HooksSection({ botId, disabled = false }: HooksSectionProps): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const [hooks, setHooks] = useState<HookInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState(false);
  const [nlInput, setNlInput] = useState("");
  const [converting, setConverting] = useState(false);
  const [preview, setPreview] = useState<HookConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedHook, setExpandedHook] = useState<string | null>(null);

  const fetchHooks = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/bots/${botId}/hooks`);
      if (res.ok) {
        const data = (await res.json()) as { hooks?: HookInfo[] };
        setHooks(data.hooks ?? []);
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, [botId]);

  useEffect(() => {
    if (expanded && !fetched) void fetchHooks();
  }, [expanded, fetched, fetchHooks]);

  const handleConvert = async (): Promise<void> => {
    if (!nlInput.trim()) return;
    setConverting(true);
    setError(null);
    setPreview(null);
    try {
      const res = await fetch("/api/hooks/nl-to-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: nlInput }),
      });
      const data = (await res.json()) as { config?: HookConfig; error?: string };
      if (res.ok && data.config) {
        setPreview(data.config);
      } else {
        setError(data.error ?? "변환에 실패했습니다");
      }
    } catch {
      setError("서버 오류가 발생했습니다");
    } finally {
      setConverting(false);
    }
  };

  const handleToggle = async (hookName: string, currentEnabled: boolean): Promise<void> => {
    const action = currentEnabled ? "disable" : "enable";
    await fetch(`/api/bots/${botId}/hooks/${encodeURIComponent(hookName)}/${action}`, {
      method: "POST",
    });
    setHooks((prev) =>
      prev.map((h) => (h.name === hookName ? { ...h, enabled: !currentEnabled } : h)),
    );
  };

  const handleDelete = async (hookName: string): Promise<void> => {
    const res = await fetch(`/api/bots/${botId}/hooks/${encodeURIComponent(hookName)}`, {
      method: "DELETE",
    });
    if (res.ok) setHooks((prev) => prev.filter((h) => h.name !== hookName));
  };

  const builtinHooks = hooks.filter((h) => h.source === "builtin");
  const customHooks = hooks.filter((h) => h.source !== "builtin");
  const enabledCount = hooks.filter((h) => h.enabled).length;

  return (
    <div className="rounded-2xl border border-black/[0.06] bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-50/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-green-50 flex items-center justify-center">
            <span className="text-sm">⚙️</span>
          </div>
          <div className="text-left">
            <p className="text-sm font-semibold text-foreground">검증 규칙</p>
            <p className="text-xs text-secondary">내부 훅 세부 설정 (고급)</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {fetched && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-green-50 text-green-600 font-medium">
              {enabledCount}/{hooks.length}
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
        <div className="border-t border-black/[0.04] px-5 py-4 space-y-4">
          {/* NL input */}
          <div className="rounded-xl border border-black/[0.06] bg-gray-50/50 px-4 py-4 space-y-3">
            <p className="text-xs text-secondary">원하는 규칙을 자연어로 설명하세요. AI가 자동으로 설정합니다.</p>
            <div className="flex gap-2">
              <input
                type="text"
                value={nlInput}
                onChange={(e) => setNlInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !converting) void handleConvert(); }}
                disabled={disabled}
                placeholder="예: 의료 관련 답변에는 반드시 면책조항을 포함해줘"
                className="flex-1 rounded-xl border border-black/[0.08] bg-white px-4 py-2.5 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-primary/30 disabled:opacity-50"
              />
              <button
                type="button"
                onClick={() => void handleConvert()}
                disabled={converting || !nlInput.trim() || disabled}
                className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {converting ? "변환 중..." : "규칙 생성"}
              </button>
            </div>
            {error && <p className="text-xs text-red-500">{error}</p>}
            {preview && (
              <div className="rounded-xl border border-blue-100 bg-blue-50/50 px-4 py-3 space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-blue-600">미리보기</p>
                  <button
                    type="button"
                    onClick={() => { setPreview(null); setNlInput(""); }}
                    className="rounded-lg bg-primary px-3 py-1 text-xs font-medium text-white hover:bg-primary/90 transition-colors"
                  >
                    추가하기
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div><span className="text-secondary">이름:</span> {preview.name}</div>
                  <div><span className="text-secondary">시점:</span> {POINT_LABEL[preview.point] ?? preview.point}</div>
                  <div><span className="text-secondary">우선순위:</span> {preview.priority}</div>
                  <div><span className="text-secondary">차단:</span> {preview.blocking ? "예" : "아니오"}</div>
                </div>
                <p className="text-xs text-secondary">{preview.description}</p>
              </div>
            )}
          </div>

          {loading ? (
            <p className="text-sm text-secondary py-6 text-center">불러오는 중...</p>
          ) : (
            <>
              {/* Custom hooks */}
              {customHooks.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-medium text-secondary">커스텀 규칙</p>
                  {customHooks.map((hook) => (
                    <HookCard
                      key={hook.name}
                      hook={hook}
                      expanded={expandedHook === hook.name}
                      onToggleExpand={() => setExpandedHook(expandedHook === hook.name ? null : hook.name)}
                      onToggle={() => void handleToggle(hook.name, hook.enabled)}
                      onDelete={() => void handleDelete(hook.name)}
                      disabled={disabled}
                      canDelete
                    />
                  ))}
                </div>
              )}

              {/* Builtin hooks */}
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-secondary">시스템 규칙 ({builtinHooks.length}개)</p>
                {builtinHooks.map((hook) => (
                  <HookCard
                    key={hook.name}
                    hook={hook}
                    expanded={expandedHook === hook.name}
                    onToggleExpand={() => setExpandedHook(expandedHook === hook.name ? null : hook.name)}
                    onToggle={() => void handleToggle(hook.name, hook.enabled)}
                    disabled={disabled}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function HookCard({
  hook,
  expanded,
  onToggleExpand,
  onToggle,
  onDelete,
  disabled = false,
  canDelete = false,
}: {
  hook: HookInfo;
  expanded: boolean;
  onToggleExpand: () => void;
  onToggle: () => void;
  onDelete?: () => void;
  disabled?: boolean;
  canDelete?: boolean;
}): React.ReactElement {
  const blockRate = hook.stats.totalRuns > 0
    ? ((hook.stats.blocks / hook.stats.totalRuns) * 100).toFixed(1)
    : "0.0";

  return (
    <div className="rounded-xl border border-black/[0.06] bg-white overflow-hidden">
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50/50 transition-colors"
        onClick={onToggleExpand}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-secondary font-mono">
              {hook.source === "builtin" ? "SYS" : "USR"}
            </span>
            <span className="text-sm font-medium truncate">
              {hook.name.replace("builtin:", "").replace("custom:", "")}
            </span>
          </div>
        </div>
        <span className="text-xs text-secondary whitespace-nowrap">{POINT_LABEL[hook.point] ?? hook.point}</span>
        <span className="text-xs text-secondary tabular-nums w-12 text-right">{blockRate}%</span>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          disabled={disabled}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            hook.enabled ? "bg-primary" : "bg-gray-200"
          } disabled:opacity-50`}
        >
          <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${hook.enabled ? "translate-x-4" : "translate-x-0.5"}`} />
        </button>
      </div>

      {expanded && (
        <div className="border-t border-black/[0.04] px-4 py-3 text-xs space-y-2 bg-gray-50/30">
          <div className="grid grid-cols-4 gap-3">
            <div><div className="text-secondary">실행</div><div className="font-medium tabular-nums">{hook.stats.totalRuns.toLocaleString()}</div></div>
            <div><div className="text-secondary">차단</div><div className="font-medium tabular-nums">{hook.stats.blocks.toLocaleString()}</div></div>
            <div><div className="text-secondary">에러</div><div className="font-medium tabular-nums">{hook.stats.errors}</div></div>
            <div><div className="text-secondary">타임아웃</div><div className="font-medium tabular-nums">{hook.stats.timeouts}</div></div>
          </div>
          <div className="flex items-center justify-between pt-1">
            <div className="text-secondary">
              우선순위 {hook.priority} · {hook.blocking ? "차단형" : "관찰형"} · {hook.failOpen ? "오류 시 통과" : "오류 시 차단"}
            </div>
            {canDelete && onDelete && (
              <button
                type="button"
                onClick={onDelete}
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
  );
}
