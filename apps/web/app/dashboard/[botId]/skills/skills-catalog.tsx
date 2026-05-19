"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import { GlassCard } from "@/components/ui/glass-card";
import { useAgentFetch } from "@/lib/local-api";

type JsonRecord = Record<string, unknown>;
type SkillDirectoryFilter = "all" | "prompt" | "script" | "hooks" | "issues";

interface SkillIssueDetail {
  key: string;
  title: string;
  reason: string;
  detail: string;
  path: string;
  lookupKeys: string[];
}

interface SkillDirectoryItem {
  name: string;
  path: string;
  tags: string[];
  promptOnly: boolean;
  scriptBacked: boolean;
  runtimeHooks: number;
  issues: SkillIssueDetail[];
}

interface SkillsSnapshot {
  loaded: JsonRecord[];
  issues: JsonRecord[];
  runtimeHooks: JsonRecord[];
}

interface SkillsCatalogProps {
  botId: string | null;
  initialDisabledSkills: string[];
  initialCustomSkills: unknown[];
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asArray(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function compactUniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function normalizedLookupKey(value: string): string {
  return value.trim().toLowerCase();
}

function normalizeSkillIssueDetails(issues: JsonRecord[]): SkillIssueDetail[] {
  return issues.map((issue, index) => {
    const skillName = asString(issue.skillName);
    const dir = asString(issue.dir);
    const path = asString(issue.path, dir);
    const title = skillName || dir || `Issue ${index + 1}`;
    const reason = asString(issue.reason, "unknown_issue");
    const detail = asString(issue.detail);
    const lookupKeys = compactUniqueStrings([skillName, dir, path, title]).map(normalizedLookupKey);
    return { key: `${title}-${reason}-${index}`, title, reason, detail, path, lookupKeys };
  });
}

function normalizeSkillDirectoryItems(loaded: JsonRecord[], issues: JsonRecord[]): SkillDirectoryItem[] {
  const issueDetails = normalizeSkillIssueDetails(issues);
  return loaded.map((skill, index) => {
    const name = asString(skill.name, `skill-${index + 1}`);
    const dir = asString(skill.dir);
    const path = asString(skill.path, dir);
    const lookupKeys = compactUniqueStrings([name, dir, path]).map(normalizedLookupKey);
    const matchedIssues = issueDetails.filter((issue) =>
      issue.lookupKeys.some((key) => lookupKeys.includes(key)),
    );
    return {
      name,
      path,
      tags: asStringArray(skill.tags),
      promptOnly: skill.promptOnly === true,
      scriptBacked: skill.scriptBacked === true,
      runtimeHooks: Math.max(0, Math.floor(asNumber(skill.runtimeHooks, 0))),
      issues: matchedIssues,
    };
  });
}

function skillSearchText(skill: SkillDirectoryItem): string {
  return [
    skill.name,
    skill.path,
    ...skill.tags,
    ...skill.issues.flatMap((issue) => [issue.title, issue.reason, issue.detail, issue.path]),
  ].join(" ").toLowerCase();
}

function filterSkillDirectoryItem(skill: SkillDirectoryItem, filter: SkillDirectoryFilter): boolean {
  if (filter === "prompt") return skill.promptOnly;
  if (filter === "script") return skill.scriptBacked;
  if (filter === "hooks") return skill.runtimeHooks > 0;
  if (filter === "issues") return skill.issues.length > 0;
  return true;
}

function skillTypeLabel(skill: SkillDirectoryItem): string {
  if (skill.scriptBacked) return "Script skill";
  if (skill.promptOnly) return "Prompt skill";
  return "Skill";
}

function SkillBadge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "primary" | "green" | "red";
}) {
  const tones = {
    neutral: "border-black/[0.08] bg-black/[0.025] text-secondary",
    primary: "border-primary/15 bg-primary/[0.08] text-primary",
    green: "border-emerald-500/20 bg-emerald-500/[0.08] text-emerald-700",
    red: "border-red-500/20 bg-red-500/[0.08] text-red-500",
  };
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold ${tones[tone]}`}>
      {children}
    </span>
  );
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-black/[0.10] bg-gray-50/70 px-4 py-8 text-center text-sm leading-6 text-secondary">
      {children}
    </div>
  );
}

function MetricTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-black/[0.04] bg-black/[0.025] px-4 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-foreground">{value}</div>
    </div>
  );
}

function FilterPill({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-full border px-3.5 py-2 text-xs font-semibold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 ${
        active
          ? "border-primary/25 bg-primary/[0.08] text-primary"
          : "border-black/[0.08] bg-white text-secondary hover:border-primary/25 hover:bg-primary/[0.035] hover:text-foreground"
      }`}
    >
      <span>{label}</span>
      <span className={`rounded-full px-2 py-0.5 text-[11px] ${active ? "bg-primary/10" : "bg-black/[0.04]"}`}>
        {count}
      </span>
    </button>
  );
}

export default function SkillsCatalog(_props: SkillsCatalogProps): React.JSX.Element {
  const agentFetch = useAgentFetch();
  const [snapshot, setSnapshot] = useState<SkillsSnapshot>({ loaded: [], issues: [], runtimeHooks: [] });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<SkillDirectoryFilter>("all");

  const applySnapshot = useCallback(async (res: Response) => {
    const data = (await res.json()) as JsonRecord;
    setSnapshot({
      loaded: asArray(data.loaded),
      issues: asArray(data.issues),
      runtimeHooks: asArray(data.runtimeHooks),
    });
  }, []);

  const loadSkills = useCallback(async () => {
    setError(null);
    const res = await agentFetch("/v1/app/skills");
    if (!res.ok) throw new Error("Failed to load local skills");
    await applySnapshot(res);
  }, [agentFetch, applySnapshot]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadSkills()
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load local skills");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadSkills]);

  const reloadSkills = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const res = await agentFetch("/v1/app/skills/reload", { method: "POST" });
      if (!res.ok) throw new Error("Failed to reload local skills");
      await applySnapshot(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reload local skills");
    } finally {
      setRefreshing(false);
    }
  }, [agentFetch, applySnapshot]);

  const skillItems = useMemo(
    () => normalizeSkillDirectoryItems(snapshot.loaded, snapshot.issues),
    [snapshot.loaded, snapshot.issues],
  );
  const issueDetails = useMemo(() => normalizeSkillIssueDetails(snapshot.issues), [snapshot.issues]);
  const hookGroups = useMemo(() => {
    const groups = new Map<string, JsonRecord[]>();
    for (const hook of snapshot.runtimeHooks) {
      const point = asString(hook.point, asString(hook.kind, "runtime"));
      groups.set(point, [...(groups.get(point) ?? []), hook]);
    }
    return Array.from(groups.entries()).map(([point, items]) => ({ point, items }));
  }, [snapshot.runtimeHooks]);

  const normalizedQuery = query.trim().toLowerCase();
  const promptSkillCount = skillItems.filter((skill) => skill.promptOnly).length;
  const scriptSkillCount = skillItems.filter((skill) => skill.scriptBacked).length;
  const hookSkillCount = skillItems.filter((skill) => skill.runtimeHooks > 0).length;
  const issueSkillCount = skillItems.filter((skill) => skill.issues.length > 0).length;
  const filterOptions: Array<{ id: SkillDirectoryFilter; label: string; count: number }> = [
    { id: "all", label: "All", count: skillItems.length },
    { id: "prompt", label: "Prompt skills", count: promptSkillCount },
    { id: "script", label: "Script skills", count: scriptSkillCount },
    { id: "hooks", label: "Runtime hooks", count: hookSkillCount },
    { id: "issues", label: "Issues", count: issueSkillCount },
  ];
  const filteredSkills = skillItems.filter((skill) => {
    if (!filterSkillDirectoryItem(skill, filter)) return false;
    if (!normalizedQuery) return true;
    return skillSearchText(skill).includes(normalizedQuery);
  });

  return (
    <div className="max-w-6xl space-y-6 pb-20">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">
            Capabilities
          </div>
          <h1 className="text-2xl font-bold leading-tight text-foreground">Skills</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">
            Local SKILL.md capabilities loaded by this runtime. This OSS view reflects the generic bundled skills
            and workspace skills reported by `/v1/app/skills`.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void reloadSkills()}
          disabled={refreshing}
          className="inline-flex min-h-[44px] cursor-pointer items-center justify-center gap-2 rounded-xl border border-black/10 bg-white px-5 py-2.5 text-sm font-semibold text-foreground transition-all duration-200 hover:border-primary/35 hover:bg-gray-50 disabled:pointer-events-none disabled:opacity-40"
        >
          <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          {refreshing ? "Reloading" : "Reload"}
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-500">
          {error}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile label="Installed" value={skillItems.length} />
        <MetricTile label="Prompt Skills" value={promptSkillCount} />
        <MetricTile label="Script Skills" value={scriptSkillCount} />
        <MetricTile label="Runtime Hooks" value={snapshot.runtimeHooks.length} />
      </div>

      {issueDetails.length > 0 && (
        <div className="rounded-2xl border border-red-500/15 bg-red-500/[0.045] px-5 py-4">
          <div className="text-sm font-semibold text-red-500">
            {issueDetails.length} skill issue{issueDetails.length === 1 ? "" : "s"} need attention
          </div>
          <p className="mt-1 text-sm leading-6 text-red-500/80">
            Invalid skill metadata stays visible here so local operators can fix it before relying on the capability.
          </p>
        </div>
      )}

      <GlassCard>
        <div className="mb-5 flex min-h-9 items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-foreground">Directory</h2>
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-secondary/60">
            {filteredSkills.length} shown
          </div>
        </div>

        <div className="mb-5 grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
          <label className="relative block">
            <span className="sr-only">Search skills</span>
            <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-secondary/40" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search skills..."
              className="min-h-11 w-full rounded-xl border border-black/[0.08] bg-white py-2.5 pl-11 pr-4 text-sm font-medium text-foreground outline-none transition-colors duration-200 placeholder:text-secondary/45 focus:border-primary/45 focus:ring-4 focus:ring-primary/10"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            {filterOptions.map((option) => (
              <FilterPill
                key={option.id}
                active={filter === option.id}
                label={option.label}
                count={option.count}
                onClick={() => setFilter(option.id)}
              />
            ))}
          </div>
        </div>

        {loading ? (
          <EmptyState>Loading skills...</EmptyState>
        ) : skillItems.length === 0 ? (
          <EmptyState>No skills loaded. Add SKILL.md directories to the local workspace, then reload.</EmptyState>
        ) : filteredSkills.length === 0 ? (
          <EmptyState>No skills match the current search or filter.</EmptyState>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {filteredSkills.map((skill) => (
              <article
                key={`${skill.name}-${skill.path}`}
                className="flex min-h-[190px] flex-col rounded-2xl border border-black/[0.06] bg-white px-4 py-4 transition-all duration-200 hover:border-primary/20 hover:bg-primary/[0.025]"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-base font-semibold text-foreground">{skill.name}</h3>
                    <p className="mt-1 truncate text-xs text-secondary">{skill.path || "workspace skill"}</p>
                  </div>
                  <SkillBadge tone={skill.scriptBacked ? "green" : "primary"}>{skillTypeLabel(skill)}</SkillBadge>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  {skill.tags.slice(0, 5).map((tag) => (
                    <SkillBadge key={tag}>{tag}</SkillBadge>
                  ))}
                  {skill.runtimeHooks > 0 && (
                    <SkillBadge tone="green">
                      {skill.runtimeHooks} hook{skill.runtimeHooks === 1 ? "" : "s"}
                    </SkillBadge>
                  )}
                  {skill.issues.length > 0 && (
                    <SkillBadge tone="red">
                      {skill.issues.length} issue{skill.issues.length === 1 ? "" : "s"}
                    </SkillBadge>
                  )}
                  {skill.tags.length === 0 && skill.runtimeHooks === 0 && skill.issues.length === 0 && (
                    <SkillBadge>no tags</SkillBadge>
                  )}
                </div>
                <div className="mt-auto pt-4">
                  <div className="rounded-xl border border-black/[0.05] bg-black/[0.025] px-3 py-2">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/60">
                      Runtime role
                    </div>
                    <p className="mt-1 text-sm leading-5 text-secondary">
                      {skill.scriptBacked
                        ? "Executable capability with an input schema and local entrypoint."
                        : "Prompt capability that can be invoked by the local operator."}
                    </p>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </GlassCard>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(320px,380px)]">
        <GlassCard>
          <h2 className="mb-4 text-sm font-semibold text-foreground">Runtime Hooks</h2>
          {hookGroups.length === 0 ? (
            <EmptyState>No runtime hooks reported.</EmptyState>
          ) : (
            <div className="space-y-3">
              {hookGroups.map((group) => (
                <div key={group.point} className="rounded-2xl border border-black/[0.06] bg-gray-50 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-sm font-semibold text-foreground">{group.point}</div>
                    <SkillBadge>{group.items.length}</SkillBadge>
                  </div>
                  <div className="mt-3 space-y-2">
                    {group.items.map((hook, index) => {
                      const hookName = asString(hook.name, asString(hook.skillName, `hook-${index + 1}`));
                      const detail = asString(hook.command, asString(hook.path, asString(hook.entry)));
                      return (
                        <div key={`${group.point}-${hookName}-${index}`} className="rounded-xl bg-white px-3 py-2">
                          <div className="text-sm font-semibold text-foreground">{hookName}</div>
                          {detail && <div className="mt-1 truncate text-xs text-secondary">{detail}</div>}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </GlassCard>

        <GlassCard>
          <h2 className="mb-4 text-sm font-semibold text-foreground">Issue detail</h2>
          {issueDetails.length === 0 ? (
            <EmptyState>No skill issues reported.</EmptyState>
          ) : (
            <div className="space-y-3">
              {issueDetails.map((issue) => (
                <div key={issue.key} className="rounded-2xl border border-red-500/15 bg-red-500/[0.04] px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-foreground">{issue.title}</div>
                      {issue.path && <div className="mt-1 truncate text-xs text-secondary">{issue.path}</div>}
                    </div>
                    <SkillBadge tone="red">{issue.reason}</SkillBadge>
                  </div>
                  {issue.detail && (
                    <p className="mt-3 rounded-xl bg-white/70 px-3 py-2 text-xs leading-5 text-secondary">
                      {issue.detail}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
