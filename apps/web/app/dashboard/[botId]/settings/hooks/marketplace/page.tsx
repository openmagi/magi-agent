"use client";

import { useState } from "react";

interface MarketplaceHook {
  id: string;
  name: string;
  displayName: string;
  description: string;
  author: string;
  category: string;
  downloads: number;
  rating: number;
  installed: boolean;
}

const SAMPLE_HOOKS: MarketplaceHook[] = [
  {
    id: "medical-safety",
    name: "custom:medical-safety",
    displayName: "의료 안전 검증",
    description: "의료 관련 응답에서 위험한 복용량 정보나 자가진단 유도를 감지하고 차단합니다",
    author: "OpenMagi",
    category: "안전",
    downloads: 1240,
    rating: 4.8,
    installed: false,
  },
  {
    id: "financial-compliance",
    name: "custom:financial-compliance",
    displayName: "금융 규정 준수",
    description: "투자 조언, 수익률 보장 등 금융 규정에 위배되는 표현을 감지합니다",
    author: "OpenMagi",
    category: "규정",
    downloads: 890,
    rating: 4.6,
    installed: false,
  },
  {
    id: "korean-formality",
    name: "custom:korean-formality",
    displayName: "한국어 격식체 유지",
    description: "응답이 적절한 존댓말과 격식체를 사용하는지 검증합니다",
    author: "Community",
    category: "언어",
    downloads: 2100,
    rating: 4.9,
    installed: false,
  },
  {
    id: "source-citation",
    name: "custom:source-citation",
    displayName: "출처 인용 검증",
    description: "사실 관련 주장에 출처가 포함되어 있는지 확인합니다",
    author: "OpenMagi",
    category: "품질",
    downloads: 1560,
    rating: 4.7,
    installed: false,
  },
  {
    id: "pii-filter",
    name: "custom:pii-filter",
    displayName: "개인정보 필터",
    description: "응답에 주민등록번호, 전화번호, 이메일 등 개인정보가 노출되지 않도록 차단합니다",
    author: "OpenMagi",
    category: "안전",
    downloads: 3200,
    rating: 4.9,
    installed: false,
  },
  {
    id: "education-scaffold",
    name: "custom:education-scaffold",
    displayName: "교육용 힌트 제공",
    description: "학습 맥락에서 직접적인 답변 대신 단계별 힌트를 제공하도록 유도합니다",
    author: "Community",
    category: "교육",
    downloads: 670,
    rating: 4.4,
    installed: false,
  },
];

const CATEGORIES = ["전체", "안전", "규정", "언어", "품질", "교육"];

export default function HookMarketplacePage() {
  const [category, setCategory] = useState("전체");
  const [search, setSearch] = useState("");
  const [hooks, setHooks] = useState(SAMPLE_HOOKS);

  const filtered = hooks.filter((h) => {
    if (category !== "전체" && h.category !== category) return false;
    if (search && !h.displayName.includes(search) && !h.description.includes(search)) return false;
    return true;
  });

  const handleInstall = (id: string) => {
    setHooks((prev) =>
      prev.map((h) => (h.id === id ? { ...h, installed: !h.installed } : h)),
    );
  };

  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <h2 className="text-xl font-semibold mb-1">규칙 마켓플레이스</h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          검증된 규칙을 찾아보고 원클릭으로 설치하세요
        </p>
      </div>

      <div className="flex gap-3 items-center">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="규칙 검색..."
          className="flex-1 max-w-xs rounded-lg border border-zinc-300 dark:border-zinc-600 bg-transparent px-4 py-2 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <div className="flex gap-1">
          {CATEGORIES.map((cat) => (
            <button
              key={cat}
              onClick={() => setCategory(cat)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                category === cat
                  ? "bg-blue-600 text-white"
                  : "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-700"
              }`}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {filtered.map((hook) => (
          <div
            key={hook.id}
            className="rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-5 space-y-3"
          >
            <div className="flex items-start justify-between">
              <div>
                <h3 className="text-sm font-semibold">{hook.displayName}</h3>
                <p className="text-xs text-zinc-400 mt-0.5">{hook.author}</p>
              </div>
              <span className="text-xs px-2 py-0.5 rounded-full bg-zinc-100 dark:bg-zinc-800 text-zinc-500">
                {hook.category}
              </span>
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
              {hook.description}
            </p>
            <div className="flex items-center justify-between pt-1">
              <div className="flex items-center gap-3 text-xs text-zinc-400">
                <span>⬇ {hook.downloads.toLocaleString()}</span>
                <span>★ {hook.rating}</span>
              </div>
              <button
                onClick={() => handleInstall(hook.id)}
                className={`px-4 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                  hook.installed
                    ? "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400"
                    : "bg-blue-600 text-white hover:bg-blue-700"
                }`}
              >
                {hook.installed ? "설치됨" : "설치"}
              </button>
            </div>
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-16 text-zinc-400 text-sm">
          검색 결과가 없습니다
        </div>
      )}

      <div className="text-center py-4">
        <p className="text-xs text-zinc-400">
          더 많은 규칙이 곧 추가됩니다. 직접 규칙을 만들어 공유할 수도 있습니다.
        </p>
      </div>
    </div>
  );
}
