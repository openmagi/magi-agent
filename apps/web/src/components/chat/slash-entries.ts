/**
 * Pure slash-autocomplete entry helpers, extracted so that unit tests can
 * import them without pulling in the JSX/React runtime.
 *
 * `chat-input.tsx` re-exports these for component-level use.
 */

import { SKILLS } from "@/lib/skills-catalog";

export interface SlashEntry {
  command: string;
  label: string;
  category: string;
  builtin?: boolean;
  searchText?: string;
}

const BUILTIN_COMMANDS: SlashEntry[] = [
  { command: "reset", label: "Reset conversation", category: "system", builtin: true },
  { command: "status", label: "Show bot status", category: "system", builtin: true },
  { command: "compact", label: "Compact memory", category: "system", builtin: true },
  { command: "help", label: "Show help", category: "system", builtin: true },
];

export const BUNDLED_SKILL_ENTRIES: SlashEntry[] = (() => {
  const entries: SlashEntry[] = [];
  for (const skill of SKILLS) {
    if (!skill.commands?.length) continue;
    for (const command of skill.commands) {
      entries.push({ command, label: skill.id, category: skill.category });
    }
  }
  return entries;
})();

export interface SlashSkill {
  name: string;
  title: string;
  description?: string;
  tags?: string[];
}

export function normalizeSlashCommand(command: string): string {
  return command.trim().replace(/^\/+/, "").replace(/\s+/g, "-");
}

/**
 * Build the full slash-autocomplete entry list.
 *
 * Precedence (higher wins dedup): builtins > customSkills > liveSkills >
 * static bundled catalog.
 *
 * @param customSkills - Hosted bot custom/learned skills (cloud path).
 * @param liveSkills   - Live skills from /v1/app/skills (local dashboard path).
 */
export function buildSlashEntries(
  customSkills: SlashSkill[] = [],
  liveSkills: SlashSkill[] = [],
): SlashEntry[] {
  const entries: SlashEntry[] = [...BUILTIN_COMMANDS];
  const seenCommands = new Set(entries.map((entry) => entry.command.toLowerCase()));

  for (const skill of customSkills) {
    const command = normalizeSlashCommand(skill.name);
    if (!command) continue;
    const dedupeKey = command.toLowerCase();
    if (seenCommands.has(dedupeKey)) continue;
    seenCommands.add(dedupeKey);
    const label = skill.title.trim() || command;
    entries.push({
      command,
      label,
      category: "custom",
      searchText: [
        command,
        label,
        skill.description ?? "",
        ...(skill.tags ?? []),
      ].join(" "),
    });
  }

  for (const skill of liveSkills) {
    const command = normalizeSlashCommand(skill.name);
    if (!command) continue;
    const dedupeKey = command.toLowerCase();
    if (seenCommands.has(dedupeKey)) continue;
    seenCommands.add(dedupeKey);
    const label = skill.title.trim() || command;
    entries.push({
      command,
      label,
      category: "skill",
      searchText: [
        command,
        label,
        skill.description ?? "",
        ...(skill.tags ?? []),
      ].join(" "),
    });
  }

  for (const entry of BUNDLED_SKILL_ENTRIES) {
    if (seenCommands.has(entry.command.toLowerCase())) continue;
    entries.push(entry);
  }

  return entries;
}

export function getSlashMatches(entries: SlashEntry[], query: string): SlashEntry[] {
  const normalizedQuery = query.toLowerCase();
  if (normalizedQuery === "") return entries.slice(0, 12);
  return entries
    .filter((entry) => {
      const haystack =
        `${entry.command} ${entry.label} ${entry.category} ${entry.searchText ?? ""}`.toLowerCase();
      return haystack.includes(normalizedQuery);
    })
    .slice(0, 12);
}
