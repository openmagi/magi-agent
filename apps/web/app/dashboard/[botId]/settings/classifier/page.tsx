"use client";

import { useState } from "react";

interface DimensionDef {
  name: string;
  phase: "request" | "finalAnswer";
  description: string;
  instructions: string;
}

export default function ClassifierSettingsPage() {
  const [dimensions, setDimensions] = useState<DimensionDef[]>([]);
  const [nlInput, setNlInput] = useState("");
  const [converting, setConverting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleAdd = async (): Promise<void> => {
    if (!nlInput.trim()) return;
    setConverting(true);
    setError(null);
    try {
      const res = await fetch("/api/hooks/nl-to-classifier", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: nlInput }),
      });
      const data = (await res.json()) as { dimension?: DimensionDef; error?: string };
      if (res.ok && data.dimension) {
        setDimensions((prev) => [...prev, data.dimension as DimensionDef]);
        setNlInput("");
      } else {
        setError(data.error ?? "변환에 실패했습니다");
      }
    } catch {
      setError("서버 오류가 발생했습니다");
    } finally {
      setConverting(false);
    }
  };

  const handleRemove = (name: string): void => {
    setDimensions((prev) => prev.filter((d) => d.name !== name));
  };

  const phaseLabel = (phase: string): string =>
    phase === "request" ? "요청 분석" : "응답 분석";

  return (
    <div className="space-y-8 max-w-4xl">
      <div>
        <h2 className="text-xl font-semibold mb-1">분류 차원 (Classifier)</h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          봇이 메시지를 분석할 때 추가로 판단할 항목을 설정합니다
        </p>
      </div>

      <div className="rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-5 space-y-4">
        <h3 className="text-base font-medium">새 분류 항목 추가</h3>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          봇이 추가로 판단해야 할 내용을 자연어로 설명하세요
        </p>
        <div className="flex gap-3">
          <input
            type="text"
            value={nlInput}
            onChange={(e) => setNlInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !converting) void handleAdd(); }}
            placeholder="예: 사용자가 의료 관련 질문을 하는지 판단해줘"
            className="flex-1 rounded-lg border border-zinc-300 dark:border-zinc-600 bg-transparent px-4 py-2.5 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            onClick={() => void handleAdd()}
            disabled={converting || !nlInput.trim()}
            className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {converting ? "변환 중..." : "추가"}
          </button>
        </div>
        {error && <p className="text-sm text-red-500">{error}</p>}
      </div>

      {dimensions.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-base font-medium">
            설정된 항목
            <span className="ml-2 text-xs text-zinc-400 font-normal">{dimensions.length}/10</span>
          </h3>
          <div className="space-y-2">
            {dimensions.map((dim) => (
              <div
                key={dim.name}
                className="flex items-center gap-3 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-4 py-3"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-medium">{dim.description || dim.name}</span>
                    <span className="text-xs px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500">
                      {phaseLabel(dim.phase)}
                    </span>
                  </div>
                  <p className="text-xs text-zinc-400 truncate">{dim.instructions}</p>
                </div>
                <button
                  onClick={() => handleRemove(dim.name)}
                  className="text-red-500 hover:text-red-600 text-xs transition-colors"
                >
                  삭제
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {dimensions.length === 0 && (
        <div className="text-center py-12 text-zinc-400 text-sm">
          아직 추가된 분류 항목이 없습니다
        </div>
      )}
    </div>
  );
}
