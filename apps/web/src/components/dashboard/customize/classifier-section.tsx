"use client";

import { useState } from "react";

export interface DimensionDef {
  name: string;
  phase: "request" | "finalAnswer";
  description: string;
  instructions: string;
}

interface ClassifierSectionProps {
  botId: string;
  dimensions: DimensionDef[];
  onDimensionsChange: (dims: DimensionDef[]) => void;
  disabled?: boolean;
}

export function ClassifierSection({
  dimensions,
  onDimensionsChange,
  disabled = false,
}: ClassifierSectionProps): React.ReactElement {
  const [expanded, setExpanded] = useState(true);
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
        onDimensionsChange([...dimensions, data.dimension]);
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
    onDimensionsChange(dimensions.filter((d) => d.name !== name));
  };

  const phaseLabel = (phase: string): string =>
    phase === "request" ? "요청 분석" : "응답 분석";

  return (
    <div className="rounded-2xl border border-black/[0.06] bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-50/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-amber-50 flex items-center justify-center">
            <span className="text-sm">🏷️</span>
          </div>
          <div className="text-left">
            <p className="text-sm font-semibold text-foreground">분류 기준</p>
            <p className="text-xs text-secondary">메시지를 어떤 기준으로 분류할까요?</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {dimensions.length > 0 && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 font-medium">
              {dimensions.length}
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
          {dimensions.map((dim) => (
            <div
              key={dim.name}
              className="flex items-center gap-3 rounded-xl border border-black/[0.06] bg-gray-50/50 px-4 py-3"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-sm font-medium">{dim.description || dim.name}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-secondary">
                    {phaseLabel(dim.phase)}
                  </span>
                </div>
                <p className="text-xs text-secondary truncate">{dim.instructions}</p>
              </div>
              <button
                type="button"
                onClick={() => handleRemove(dim.name)}
                disabled={disabled}
                className="text-xs text-red-400 hover:text-red-500 transition-colors disabled:opacity-40"
              >
                삭제
              </button>
            </div>
          ))}

          {dimensions.length === 0 && (
            <div className="text-center py-6 text-secondary text-xs">
              아직 추가된 분류 기준이 없습니다
            </div>
          )}

          <div className="flex gap-2">
            <input
              type="text"
              value={nlInput}
              onChange={(e) => setNlInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !converting) void handleAdd(); }}
              disabled={disabled}
              placeholder="예: 사용자가 의료 관련 질문을 하는지 판단해줘"
              className="flex-1 rounded-xl border border-black/[0.08] bg-white px-4 py-2.5 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-primary/30 transition-colors disabled:opacity-50"
            />
            <button
              type="button"
              onClick={() => void handleAdd()}
              disabled={converting || !nlInput.trim() || disabled}
              className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {converting ? "변환 중..." : "추가"}
            </button>
          </div>
          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>
      )}
    </div>
  );
}
