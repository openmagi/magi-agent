"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";

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

interface HookCardProps {
  hook: HookInfo;
  expanded: boolean;
  onToggleExpand: () => void;
  onToggle: () => void;
  onDelete?: () => void;
  pointLabel: (p: string) => string;
  canDelete?: boolean;
}

function HookCard({ hook, expanded, onToggleExpand, onToggle, onDelete, pointLabel, canDelete }: HookCardProps) {
  const blockRate = hook.stats.totalRuns > 0
    ? ((hook.stats.blocks / hook.stats.totalRuns) * 100).toFixed(1)
    : "0.0";

  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 overflow-hidden">
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors"
        onClick={onToggleExpand}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 font-mono">
              {hook.source === "builtin" ? "SYS" : "USR"}
            </span>
            <span className="text-sm font-medium truncate">
              {hook.name.replace("builtin:", "").replace("custom:", "")}
            </span>
          </div>
        </div>
        <span className="text-xs text-zinc-400 whitespace-nowrap">{pointLabel(hook.point)}</span>
        <span className="text-xs text-zinc-400 tabular-nums w-12 text-right">{blockRate}%</span>
        <button
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            hook.enabled ? "bg-blue-600" : "bg-zinc-300 dark:bg-zinc-600"
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
        <div className="border-t border-zinc-100 dark:border-zinc-800 px-4 py-3 text-xs space-y-2 bg-zinc-50/50 dark:bg-zinc-800/30">
          <div className="grid grid-cols-4 gap-3">
            <div>
              <div className="text-zinc-400">실행</div>
              <div className="font-medium tabular-nums">{hook.stats.totalRuns.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-zinc-400">차단</div>
              <div className="font-medium tabular-nums">{hook.stats.blocks.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-zinc-400">에러</div>
              <div className="font-medium tabular-nums">{hook.stats.errors}</div>
            </div>
            <div>
              <div className="text-zinc-400">타임아웃</div>
              <div className="font-medium tabular-nums">{hook.stats.timeouts}</div>
            </div>
          </div>
          <div className="flex items-center justify-between pt-1">
            <div className="text-zinc-400">
              우선순위 {hook.priority} · {hook.blocking ? "차단형" : "관찰형"} · {hook.failOpen ? "오류 시 통과" : "오류 시 차단"}
            </div>
            {canDelete && onDelete && (
              <button
                onClick={onDelete}
                className="text-red-500 hover:text-red-600 text-xs transition-colors"
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

export default function HooksSettingsPage() {
  const params = useParams();
  const botId = params.botId as string;
  const [hooks, setHooks] = useState<HookInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [nlInput, setNlInput] = useState("");
  const [converting, setConverting] = useState(false);
  const [preview, setPreview] = useState<HookConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedHook, setExpandedHook] = useState<string | null>(null);

  const fetchHooks = useCallback(async () => {
    try {
      const res = await fetch(`/api/bots/${botId}/hooks`);
      if (res.ok) {
        const data = (await res.json()) as { hooks?: HookInfo[] };
        setHooks(data.hooks ?? []);
      }
    } catch {
      // ignore fetch errors — hooks list may not be available yet
    } finally {
      setLoading(false);
    }
  }, [botId]);

  useEffect(() => {
    fetchHooks();
  }, [fetchHooks]);

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
    if (res.ok) {
      setHooks((prev) => prev.filter((h) => h.name !== hookName));
    }
  };

  const pointLabel = (point: string): string => {
    const labels: Record<string, string> = {
      beforeTurnStart: "턴 시작 전",
      beforeLLMCall: "LLM 호출 전",
      afterLLMCall: "LLM 호출 후",
      beforeToolUse: "도구 사용 전",
      afterToolUse: "도구 사용 후",
      beforeCommit: "응답 확정 전",
      afterCommit: "응답 확정 후",
      afterTurnEnd: "턴 종료 후",
    };
    return labels[point] ?? point;
  };

  const builtinHooks = hooks.filter((h) => h.source === "builtin");
  const customHooks = hooks.filter((h) => h.source !== "builtin");

  return (
    <div className="space-y-8 max-w-4xl">
      <div>
        <h2 className="text-xl font-semibold mb-1">검증 규칙 (Hooks)</h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          봇의 응답을 자동으로 검증하는 규칙을 관리합니다
        </p>
      </div>

      {/* Natural language input */}
      <div className="rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-5 space-y-4">
        <h3 className="text-base font-medium">새 규칙 추가</h3>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          원하는 규칙을 자연어로 설명하세요. AI가 자동으로 설정해드립니다.
        </p>
        <div className="flex gap-3">
          <input
            type="text"
            value={nlInput}
            onChange={(e) => setNlInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !converting) void handleConvert(); }}
            placeholder="예: 의료 관련 답변에는 반드시 면책조항을 포함해줘"
            className="flex-1 rounded-lg border border-zinc-300 dark:border-zinc-600 bg-transparent px-4 py-2.5 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            onClick={() => void handleConvert()}
            disabled={converting || !nlInput.trim()}
            className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {converting ? "변환 중..." : "규칙 생성"}
          </button>
        </div>

        {error && (
          <p className="text-sm text-red-500">{error}</p>
        )}

        {preview && (
          <div className="rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/30 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-medium text-blue-700 dark:text-blue-400">미리보기</h4>
              <button
                onClick={() => { setPreview(null); setNlInput(""); }}
                className="rounded-lg bg-blue-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-blue-700 transition-colors"
              >
                추가하기
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div><span className="text-zinc-500">이름:</span> {preview.name}</div>
              <div><span className="text-zinc-500">시점:</span> {pointLabel(preview.point)}</div>
              <div><span className="text-zinc-500">우선순위:</span> {preview.priority}</div>
              <div><span className="text-zinc-500">차단:</span> {preview.blocking ? "예" : "아니오"}</div>
            </div>
            <p className="text-xs text-zinc-600 dark:text-zinc-400">{preview.description}</p>
            <p className="text-xs text-zinc-500 italic">{preview.checkLogic}</p>
          </div>
        )}
      </div>

      {/* Custom hooks */}
      {customHooks.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-base font-medium">커스텀 규칙</h3>
          <div className="space-y-2">
            {customHooks.map((hook) => (
              <HookCard
                key={hook.name}
                hook={hook}
                expanded={expandedHook === hook.name}
                onToggleExpand={() => setExpandedHook(expandedHook === hook.name ? null : hook.name)}
                onToggle={() => void handleToggle(hook.name, hook.enabled)}
                onDelete={() => void handleDelete(hook.name)}
                pointLabel={pointLabel}
                canDelete
              />
            ))}
          </div>
        </div>
      )}

      {/* Builtin hooks */}
      <div className="space-y-3">
        <h3 className="text-base font-medium">
          시스템 규칙
          <span className="ml-2 text-xs text-zinc-400 font-normal">
            {builtinHooks.length}개
          </span>
        </h3>
        {loading ? (
          <p className="text-sm text-zinc-400">불러오는 중...</p>
        ) : (
          <div className="space-y-2">
            {builtinHooks.map((hook) => (
              <HookCard
                key={hook.name}
                hook={hook}
                expanded={expandedHook === hook.name}
                onToggleExpand={() => setExpandedHook(expandedHook === hook.name ? null : hook.name)}
                onToggle={() => void handleToggle(hook.name, hook.enabled)}
                pointLabel={pointLabel}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
