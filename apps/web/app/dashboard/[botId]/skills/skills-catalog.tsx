"use client";

import { useState, useMemo, useCallback, useRef } from "react";
import { GlassCard } from "@/components/ui/glass-card";
import { CATEGORIES, SKILLS, CORE_SKILLS } from "@/lib/skills-catalog";
import type { SkillDef, SkillCategory } from "@/lib/skills-catalog";
import { useMessages, useI18n } from "@/lib/i18n";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
/** Inline type for custom skill list items (original module deleted in OSS trim). */
interface CustomSkillListItem {
  name: string;
  description?: string;
  source?: string;
  installed_at?: string;
}

const IS_KOREAN_RE = /[가-힣]/;

/** Pick the locale-appropriate command: Korean users see Korean aliases, others see ASCII ones. */
function getDisplayCommand(commands: string[] | undefined, id: string, locale: string): string {
  if (!commands?.length) return id;
  if (locale === "ko") {
    return commands.find((c) => IS_KOREAN_RE.test(c)) ?? commands[0];
  }
  return commands.find((c) => !IS_KOREAN_RE.test(c)) ?? id;
}

/** For Korean locale, return the English command if the primary display is Korean (and vice versa). */
function getAltCommand(commands: string[] | undefined, id: string, locale: string): string | null {
  if (!commands?.length) return null;
  const primary = getDisplayCommand(commands, id, locale);
  if (locale === "ko" && IS_KOREAN_RE.test(primary)) {
    const eng = commands.find((c) => !IS_KOREAN_RE.test(c));
    return eng ?? null;
  }
  if (locale !== "ko" && !IS_KOREAN_RE.test(primary)) {
    const kor = commands.find((c) => IS_KOREAN_RE.test(c));
    return kor ?? null;
  }
  return null;
}

function CopyChip({ text, children, className }: { text: string; children: React.ReactNode; className?: string }) {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout>>(null);

  const handleCopy = useCallback(() => {
    void navigator.clipboard.writeText(text);
    setCopied(true);
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setCopied(false), 1500);
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      className={`relative cursor-pointer transition-colors group ${className ?? ""}`}
      title="Click to copy"
    >
      {children}
      {copied && (
        <span className="absolute -top-7 left-1/2 -translate-x-1/2 px-2 py-0.5 rounded bg-gray-800 text-white text-[11px] font-medium whitespace-nowrap animate-fade-in pointer-events-none">
          Copied!
        </span>
      )}
    </button>
  );
}

type SkillText = {
  name: string;
  description: string;
  examples: readonly string[];
  details?: string;
};

function CategoryBadge({ category, label }: { category: SkillCategory; label: string }) {
  const meta = CATEGORIES.find((c) => c.id === category);
  if (!meta) return null;
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 text-xs font-medium rounded-full border ${meta.color}`}>
      {label}
    </span>
  );
}

function SimpleMarkdown({ content }: { content: string }): React.JSX.Element {
  const lines = content.split("\n");
  const elements: React.ReactNode[] = [];
  let i = 0;
  let key = 0;

  const renderInline = (text: string): React.ReactNode[] => {
    const parts: React.ReactNode[] = [];
    const regex = /(\*\*(.+?)\*\*|`([^`]+)`)/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;
    let inlineKey = 0;

    while ((match = regex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parts.push(text.slice(lastIndex, match.index));
      }
      if (match[2]) {
        parts.push(<strong key={inlineKey++}>{match[2]}</strong>);
      } else if (match[3]) {
        parts.push(
          <code key={inlineKey++} className="px-1 py-0.5 bg-gray-200 rounded text-xs font-mono">
            {match[3]}
          </code>
        );
      }
      lastIndex = regex.lastIndex;
    }
    if (lastIndex < text.length) {
      parts.push(text.slice(lastIndex));
    }
    return parts;
  };

  while (i < lines.length) {
    const line = lines[i];

    // Heading
    if (line.startsWith("### ")) {
      elements.push(
        <h4 key={key++} className="font-semibold text-gray-900 mt-4 mb-2 text-sm">
          {renderInline(line.slice(4))}
        </h4>
      );
      i++;
      continue;
    }

    // Table
    if (line.includes("|") && i + 1 < lines.length && /^\|[\s-:|]+\|$/.test(lines[i + 1].trim())) {
      const parseRow = (row: string): string[] =>
        row.split("|").slice(1, -1).map((c) => c.trim());
      const headers = parseRow(line);
      i += 2; // skip header + separator
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|")) {
        rows.push(parseRow(lines[i]));
        i++;
      }
      elements.push(
        <div key={key++} className="overflow-x-auto my-2">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-200">
                {headers.map((h, hi) => (
                  <th key={hi} className="text-left px-2 py-1.5 font-semibold text-gray-900">
                    {renderInline(h)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className={ri % 2 === 1 ? "bg-gray-50" : ""}>
                  {row.map((cell, ci) => (
                    <td key={ci} className="px-2 py-1.5 text-gray-700">
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    // List items
    if (/^[\s]*[-*]\s/.test(line)) {
      const items: React.ReactNode[] = [];
      while (i < lines.length && /^[\s]*[-*]\s/.test(lines[i])) {
        items.push(
          <li key={items.length}>{renderInline(lines[i].replace(/^[\s]*[-*]\s/, ""))}</li>
        );
        i++;
      }
      elements.push(
        <ul key={key++} className="list-disc pl-4 space-y-1">
          {items}
        </ul>
      );
      continue;
    }

    // Empty line
    if (line.trim() === "") {
      i++;
      continue;
    }

    // Paragraph — collect consecutive non-special lines
    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !lines[i].startsWith("### ") &&
      !/^[\s]*[-*]\s/.test(lines[i]) &&
      !(lines[i].includes("|") && i + 1 < lines.length && /^\|[\s-:|]+\|$/.test((lines[i + 1] ?? "").trim()))
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    if (paraLines.length > 0) {
      elements.push(
        <p key={key++}>{renderInline(paraLines.join(" "))}</p>
      );
    }
  }

  return <>{elements}</>;
}

function SkillModal({
  skill,
  onClose,
  getText,
  getCategoryLabel,
  trySayingLabel,
  relatedLabel,
  showDetailsLabel,
  hideDetailsLabel,
}: {
  skill: SkillDef;
  onClose: () => void;
  getText: (id: string) => SkillText;
  getCategoryLabel: (cat: SkillCategory) => string;
  trySayingLabel: string;
  relatedLabel: string;
  showDetailsLabel: string;
  hideDetailsLabel: string;
}) {
  const { locale } = useI18n();
  const [current, setCurrent] = useState(skill);
  const [detailsOpen, setDetailsOpen] = useState(false);

  const handleRelatedClick = useCallback((s: SkillDef) => {
    setCurrent(s);
    setDetailsOpen(false);
  }, []);

  const currentRelated = useMemo(
    () => (current.related ?? []).map((id) => SKILLS.find((s) => s.id === id)).filter(Boolean) as SkillDef[],
    [current.related]
  );

  const text = getText(current.id);
  const displayCmd = getDisplayCommand(current.commands, current.id, locale);
  const altCmd = getAltCommand(current.commands, current.id, locale);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-lg max-h-[80vh] overflow-y-auto bg-white rounded-2xl p-6 sm:p-8 shadow-2xl border border-gray-200"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors cursor-pointer"
          aria-label="Close"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M1 1l12 12M13 1L1 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></svg>
        </button>

        <div className="mb-5">
          <div className="flex items-center gap-3 mb-3">
            <h2 className="text-xl font-bold text-gray-900">{text.name}</h2>
            <CategoryBadge category={current.category} label={getCategoryLabel(current.category)} />
          </div>
          <p className="text-gray-600 leading-relaxed text-[15px]">{text.description}</p>
        </div>

        {/* Slash command */}
        <div className="mb-5 flex items-center gap-2">
          <CopyChip
            text={`/${displayCmd}`}
            className="inline-flex items-center px-3 py-1.5 rounded-lg bg-gray-50 border border-gray-200 text-sm font-mono text-gray-700 hover:bg-gray-100"
          >
            /{displayCmd}
          </CopyChip>
          {altCmd && (
            <CopyChip
              text={`/${altCmd}`}
              className="inline-flex items-center px-3 py-1.5 rounded-lg bg-gray-50 border border-gray-200 text-sm font-mono text-gray-400 hover:bg-gray-100"
            >
              /{altCmd}
            </CopyChip>
          )}
        </div>

        <div className="mb-5">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2.5">
            {trySayingLabel}
          </h3>
          <div className="space-y-2">
            {text.examples.map((ex) => (
              <CopyChip
                key={ex}
                text={ex}
                className="block w-full text-left bg-gray-50 rounded-xl px-4 py-3 text-sm text-gray-600 border border-gray-100 hover:bg-gray-100 hover:border-gray-200"
              >
                &ldquo;{ex}&rdquo;
              </CopyChip>
            ))}
          </div>
        </div>

        {/* Expandable details */}
        {text.details && (
          <div className="mb-5">
            <button
              onClick={() => setDetailsOpen((v) => !v)}
              className="flex items-center gap-2 text-sm font-medium text-primary hover:text-primary/80 transition-colors cursor-pointer"
            >
              <svg
                width="12" height="12" viewBox="0 0 12 12" fill="none"
                className={`transition-transform duration-200 ${detailsOpen ? "rotate-90" : ""}`}
              >
                <path d="M4 2l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              {detailsOpen ? hideDetailsLabel : showDetailsLabel}
            </button>
            {detailsOpen && (
              <div className="mt-3 space-y-2 text-sm text-gray-700 leading-relaxed animate-fade-in">
                <SimpleMarkdown content={text.details} />
              </div>
            )}
          </div>
        )}

        {currentRelated.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2.5">
              {relatedLabel}
            </h3>
            <div className="flex flex-wrap gap-2">
              {currentRelated.map((rs) => (
                <button
                  key={rs.id}
                  onClick={() => handleRelatedClick(rs)}
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-50 border border-gray-200 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 transition-colors cursor-pointer"
                >
                  {getText(rs.id).name}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ToggleSwitch({
  enabled,
  disabled,
  title,
  onToggle,
}: {
  enabled: boolean;
  disabled: boolean;
  title: string;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onToggle(); }}
      disabled={disabled}
      title={title}
      className={`relative shrink-0 w-10 h-6 rounded-full transition-colors cursor-pointer ${
        disabled ? "opacity-40 cursor-not-allowed" : ""
      } ${enabled ? "bg-primary" : "bg-gray-300"}`}
    >
      <span
        className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
          enabled ? "translate-x-4" : "translate-x-0"
        }`}
      />
    </button>
  );
}

function SkillCard({
  skill,
  onClick,
  text,
  categoryLabel,
  isEnabled,
  isCore,
  onToggle,
}: {
  skill: SkillDef;
  onClick: () => void;
  text: SkillText;
  categoryLabel: string;
  isEnabled: boolean;
  isCore: boolean;
  onToggle: () => void;
}) {
  const { locale } = useI18n();
  return (
    <GlassCard hover className={`min-w-0 flex flex-col gap-3 transition-opacity ${!isEnabled ? "opacity-50" : ""}`}>
      <div className="flex items-start justify-between gap-2">
        <div onClick={onClick} className="min-w-0 flex-1 cursor-pointer">
          <div className="mb-2 flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
            <h3 className="min-w-0 text-base font-semibold text-foreground leading-snug break-words">{text.name}</h3>
            <span className="min-w-0 break-all text-xs font-mono text-gray-400">
              /{getDisplayCommand(skill.commands, skill.id, locale)}
              {getAltCommand(skill.commands, skill.id, locale) && (
                <span className="text-gray-300">{" "}/{getAltCommand(skill.commands, skill.id, locale)}</span>
              )}
            </span>
          </div>
          <CategoryBadge category={skill.category} label={categoryLabel} />
        </div>
        <ToggleSwitch
          enabled={isEnabled}
          disabled={isCore}
          title={isCore ? "Core skill — cannot be disabled" : isEnabled ? "Click to disable" : "Click to enable"}
          onToggle={onToggle}
        />
      </div>
      <div onClick={onClick} className="cursor-pointer">
        <p className="text-sm text-gray-600 leading-relaxed mb-3 break-words">{text.description}</p>
        <div className="space-y-1.5">
          {text.examples.slice(0, 2).map((ex) => (
            <div key={ex} className="text-xs text-gray-600 bg-gray-50 rounded-lg px-3 py-2 border border-gray-200 break-words">
              &ldquo;{ex}&rdquo;
            </div>
          ))}
        </div>
      </div>
    </GlassCard>
  );
}

interface SkillsCatalogProps {
  botId: string | null;
  initialDisabledSkills: string[];
  initialCustomSkills: CustomSkillListItem[];
}

const EMPTY_CUSTOM_SKILL = {
  title: "",
  description: "",
  body: "",
  tags: "",
};

export default function SkillsCatalog({
  botId,
  initialDisabledSkills,
  initialCustomSkills,
}: SkillsCatalogProps): React.JSX.Element {
  const t = useMessages();
  const sc = t.skillsCatalog;
  const cs = sc.customSkills;
  const authFetch = useAuthFetch();
  const [activeCategory, setActiveCategory] = useState<SkillCategory | "all">("all");
  const [search, setSearch] = useState("");
  const [selectedSkill, setSelectedSkill] = useState<SkillDef | null>(null);
  const [disabledSkills, setDisabledSkills] = useState<Set<string>>(new Set(initialDisabledSkills));
  const [savedDisabledSkills, setSavedDisabledSkills] = useState<Set<string>>(new Set(initialDisabledSkills));
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [customSkills, setCustomSkills] = useState<CustomSkillListItem[]>(initialCustomSkills);
  const [customDraft, setCustomDraft] = useState(EMPTY_CUSTOM_SKILL);
  const [customSaving, setCustomSaving] = useState(false);
  const [deletingCustomSkill, setDeletingCustomSkill] = useState<string | null>(null);

  const hasChanges = useMemo(() => {
    if (disabledSkills.size !== savedDisabledSkills.size) return true;
    for (const s of disabledSkills) {
      if (!savedDisabledSkills.has(s)) return true;
    }
    return false;
  }, [disabledSkills, savedDisabledSkills]);

  const enabledCount = SKILLS.length - disabledSkills.size;

  const handleToggle = useCallback((skillId: string) => {
    if (CORE_SKILLS.has(skillId)) return;
    setDisabledSkills((prev) => {
      const next = new Set(prev);
      if (next.has(skillId)) {
        next.delete(skillId);
      } else {
        next.add(skillId);
      }
      return next;
    });
  }, []);

  const handleSave = useCallback(async () => {
    if (!botId || !hasChanges) return;
    setSaving(true);
    try {
      const res = await authFetch(`/api/bots/${botId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ disabled_skills: Array.from(disabledSkills) }),
      });
      if (res.ok) {
        setSavedDisabledSkills(new Set(disabledSkills));
        setToast("Skills updated");
        setTimeout(() => setToast(null), 2000);
      } else {
        setToast("Failed to update");
        setTimeout(() => setToast(null), 3000);
      }
    } catch {
      setToast("Failed to update");
      setTimeout(() => setToast(null), 3000);
    } finally {
      setSaving(false);
    }
  }, [botId, disabledSkills, hasChanges, authFetch]);

  const refreshRuntimeSkills = useCallback(async () => {
    if (!botId) return;
    const res = await authFetch(`/api/bots/${botId}/skills/refresh`, {
      method: "POST",
    });
    if (!res.ok) {
      throw new Error("Skill refresh failed");
    }
  }, [authFetch, botId]);

  const handleInstallCustomSkill = useCallback(async () => {
    if (!botId || customSaving) return;
    setCustomSaving(true);
    try {
      const res = await authFetch(`/api/bots/${botId}/custom-skills`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: customDraft.title,
          description: customDraft.description,
          body: customDraft.body,
          tags: customDraft.tags
            .split(",")
            .map((tag) => tag.trim())
            .filter(Boolean),
        }),
      });
      if (!res.ok) {
        setToast(cs.installFailure);
        setTimeout(() => setToast(null), 3000);
        return;
      }
      const json = (await res.json()) as { skill: CustomSkillListItem };
      setCustomSkills((prev) => {
        const withoutExisting = prev.filter((skill) => skill.name !== json.skill.name);
        return [json.skill, ...withoutExisting];
      });
      setCustomDraft(EMPTY_CUSTOM_SKILL);
      await refreshRuntimeSkills();
      setToast(cs.installSuccess);
      setTimeout(() => setToast(null), 2500);
    } catch {
      setToast(cs.installFailure);
      setTimeout(() => setToast(null), 3000);
    } finally {
      setCustomSaving(false);
    }
  }, [
    authFetch,
    botId,
    cs.installFailure,
    cs.installSuccess,
    customDraft,
    customSaving,
    refreshRuntimeSkills,
  ]);

  const handleDeleteCustomSkill = useCallback(async (skillName: string) => {
    if (!botId || deletingCustomSkill) return;
    setDeletingCustomSkill(skillName);
    try {
      const res = await authFetch(`/api/bots/${botId}/custom-skills/${skillName}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        setToast(cs.deleteFailure);
        setTimeout(() => setToast(null), 3000);
        return;
      }
      setCustomSkills((prev) => prev.filter((skill) => skill.name !== skillName));
      await refreshRuntimeSkills();
      setToast(cs.deleteSuccess);
      setTimeout(() => setToast(null), 2500);
    } catch {
      setToast(cs.deleteFailure);
      setTimeout(() => setToast(null), 3000);
    } finally {
      setDeletingCustomSkill(null);
    }
  }, [
    authFetch,
    botId,
    cs.deleteFailure,
    cs.deleteSuccess,
    deletingCustomSkill,
    refreshRuntimeSkills,
  ]);

  const getText = useCallback(
    (id: string): SkillText => {
      const entry = sc.skills[id as keyof typeof sc.skills];
      if (entry) return entry as unknown as SkillText;
      return { name: id, description: "", examples: [], details: "" };
    },
    [sc]
  );

  const getCategoryLabel = useCallback(
    (cat: SkillCategory): string => {
      return sc.categories[cat as keyof typeof sc.categories] ?? cat;
    },
    [sc]
  );

  const filtered = useMemo(() => {
    let result = SKILLS;
    if (activeCategory !== "all") {
      result = result.filter((s) => s.category === activeCategory);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter((s) => {
        const text = getText(s.id);
        return text.name.toLowerCase().includes(q) || text.description.toLowerCase().includes(q);
      });
    }
    return result;
  }, [activeCategory, search, getText]);

  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = { all: SKILLS.length };
    for (const s of SKILLS) {
      counts[s.category] = (counts[s.category] ?? 0) + 1;
    }
    return counts;
  }, []);

  const canInstallCustomSkill =
    customDraft.title.trim().length > 1 &&
    customDraft.description.trim().length > 7 &&
    customDraft.body.trim().length > 7 &&
    Boolean(botId);

  return (
    <div className="min-w-0 space-y-6 sm:space-y-8 pb-24">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-2xl sm:text-3xl font-bold text-foreground mb-2">{sc.pageTitle}</h1>
          <p className="text-secondary break-words">{sc.pageSubtitle}</p>
        </div>
        <span className="text-sm text-secondary shrink-0">
          {enabledCount}/{SKILLS.length} enabled
        </span>
      </div>

      <section className="min-w-0 rounded-2xl border border-gray-200 bg-white p-4 sm:p-5 shadow-sm">
        <div className="mb-4 flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-foreground">{cs.title}</h2>
            <p className="mt-1 text-sm text-secondary">
              {cs.subtitle}
            </p>
          </div>
          <span className="shrink-0 rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary-light">
            {customSkills.length} {cs.installed}
          </span>
        </div>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(280px,360px)]">
          <div className="min-w-0 space-y-3">
            {customSkills.length === 0 ? (
              <div className="rounded-xl border border-dashed border-gray-300 p-4 text-sm text-secondary">
                {cs.empty}
              </div>
            ) : (
              customSkills.map((skill) => (
                <div
                  key={skill.name}
                  className="min-w-0 rounded-xl border border-gray-200 bg-gray-50 p-4"
                >
                  <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <h3 className="min-w-0 break-words text-sm font-semibold text-foreground">
                          {skill.title}
                        </h3>
                        <span className="break-all rounded bg-white px-2 py-0.5 text-xs font-mono text-gray-500">
                          {skill.name}
                        </span>
                      </div>
                      <p className="mt-2 break-words text-sm text-gray-600">
                        {skill.description}
                      </p>
                      {skill.tags.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {skill.tags.map((tag) => (
                            <span
                              key={tag}
                              className="rounded-full bg-white px-2 py-0.5 text-xs text-gray-500"
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => handleDeleteCustomSkill(skill.name)}
                      disabled={deletingCustomSkill === skill.name}
                      className="shrink-0 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-600 transition-colors hover:bg-white disabled:opacity-50"
                      aria-label={`${cs.delete} ${skill.title}`}
                    >
                      {cs.delete}
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="min-w-0 rounded-xl border border-gray-200 bg-gray-50 p-4">
            <h3 className="text-sm font-semibold text-foreground">{cs.installTitle}</h3>
            <div className="mt-3 space-y-3">
              <input
                value={customDraft.title}
                onChange={(e) => setCustomDraft((prev) => ({ ...prev, title: e.target.value }))}
                placeholder={cs.titlePlaceholder}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-foreground placeholder:text-gray-400 focus:border-primary/30 focus:outline-none"
              />
              <input
                value={customDraft.description}
                onChange={(e) => setCustomDraft((prev) => ({ ...prev, description: e.target.value }))}
                placeholder={cs.descriptionPlaceholder}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-foreground placeholder:text-gray-400 focus:border-primary/30 focus:outline-none"
              />
              <textarea
                value={customDraft.body}
                onChange={(e) => setCustomDraft((prev) => ({ ...prev, body: e.target.value }))}
                placeholder={cs.bodyPlaceholder}
                rows={7}
                className="w-full resize-y rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-foreground placeholder:text-gray-400 focus:border-primary/30 focus:outline-none"
              />
              <input
                value={customDraft.tags}
                onChange={(e) => setCustomDraft((prev) => ({ ...prev, tags: e.target.value }))}
                placeholder={cs.tagsPlaceholder}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-foreground placeholder:text-gray-400 focus:border-primary/30 focus:outline-none"
              />
              <button
                onClick={handleInstallCustomSkill}
                disabled={!canInstallCustomSkill || customSaving}
                className="w-full rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {customSaving ? cs.installing : cs.install}
              </button>
            </div>
          </div>
        </div>
      </section>

      <div className="flex min-w-0 flex-col gap-4 sm:flex-row">
        <div className="flex min-w-0 overflow-x-auto gap-2 pb-1 scrollbar-hide flex-1">
          <button
            onClick={() => setActiveCategory("all")}
            className={`shrink-0 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors cursor-pointer ${
              activeCategory === "all"
                ? "bg-primary/10 text-primary-light border border-primary/20"
                : "text-gray-600 hover:text-gray-900 hover:bg-gray-100 border border-transparent"
            }`}
          >
            {sc.allTab} ({categoryCounts.all})
          </button>
          {CATEGORIES.map((cat) => (
            <button
              key={cat.id}
              onClick={() => setActiveCategory(cat.id)}
              className={`shrink-0 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors cursor-pointer ${
                activeCategory === cat.id
                  ? "bg-primary/10 text-primary-light border border-primary/20"
                  : "text-gray-600 hover:text-gray-900 hover:bg-gray-100 border border-transparent"
              }`}
            >
              {getCategoryLabel(cat.id)} ({categoryCounts[cat.id] ?? 0})
            </button>
          ))}
        </div>

        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={sc.searchPlaceholder}
          className="w-full sm:w-56 sm:shrink-0 px-4 py-2 rounded-xl bg-white border border-gray-300 text-sm text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/30 transition-colors"
        />
      </div>

      {filtered.length === 0 ? (
        <div className="text-center py-16 text-secondary">{sc.noResults}</div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-3 gap-4 sm:gap-5">
          {filtered.map((skill) => (
            <SkillCard
              key={skill.id}
              skill={skill}
              onClick={() => setSelectedSkill(skill)}
              text={getText(skill.id)}
              categoryLabel={getCategoryLabel(skill.category)}
              isEnabled={!disabledSkills.has(skill.id)}
              isCore={CORE_SKILLS.has(skill.id)}
              onToggle={() => handleToggle(skill.id)}
            />
          ))}
        </div>
      )}

      {/* Sticky save bar */}
      {hasChanges && botId && (
        <div className="fixed bottom-0 left-0 right-0 z-40 border-t border-gray-200 bg-white/90 backdrop-blur-sm px-6 py-4">
          <div className="max-w-5xl mx-auto flex items-center justify-between">
            <span className="text-sm text-secondary">
              You have unsaved changes
            </span>
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-6 py-2 rounded-xl bg-primary text-white font-medium text-sm hover:bg-primary/90 disabled:opacity-50 transition-colors cursor-pointer"
            >
              {saving ? "Updating..." : "Save"}
            </button>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-20 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-xl bg-gray-900 text-white text-sm font-medium shadow-lg animate-fade-in">
          {toast}
        </div>
      )}

      {selectedSkill && (
        <SkillModal
          skill={selectedSkill}
          onClose={() => setSelectedSkill(null)}
          getText={getText}
          getCategoryLabel={getCategoryLabel}
          trySayingLabel={sc.trySaying}
          relatedLabel={sc.relatedSkills}
          showDetailsLabel={sc.showDetails}
          hideDetailsLabel={sc.hideDetails}
        />
      )}
    </div>
  );
}
