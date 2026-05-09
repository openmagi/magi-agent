import path from "node:path";
import type { ChannelMemoryMode } from "./types.js";

const PROTECTED_TOP_LEVEL_FILES = new Set([
  "MEMORY.md",
  "SCRATCHPAD.md",
  "WORKING.md",
  "TASK-QUEUE.md",
]);

export function normalizeMemoryMode(value: unknown): ChannelMemoryMode {
  if (value === "read_only") return "read_only";
  return value === "incognito" ? "incognito" : "normal";
}

export function isIncognitoMemoryMode(value: unknown): boolean {
  return normalizeMemoryMode(value) === "incognito";
}

export function isLongTermMemoryWriteDisabled(value: unknown): boolean {
  const mode = normalizeMemoryMode(value);
  return mode === "read_only" || mode === "incognito";
}

export function isProtectedMemoryPath(rawPath: string | undefined | null): boolean {
  if (!rawPath) return false;
  const normalized = path.posix
    .normalize(rawPath.replace(/\\/g, "/"))
    .replace(/^\.\/+/, "")
    .replace(/^\/+/, "");
  if (normalized === "." || normalized === "") return false;
  if (normalized === "memory" || normalized.startsWith("memory/")) return true;
  return PROTECTED_TOP_LEVEL_FILES.has(normalized);
}

export function commandMentionsProtectedMemory(command: string): boolean {
  if (!command) return false;
  return (
    /\bmemory(?:\/|\b)/i.test(command) ||
    /\b(?:MEMORY\.md|SCRATCHPAD\.md|WORKING\.md|TASK-QUEUE\.md)\b/.test(command)
  );
}

export function commandMayWriteProtectedMemory(command: string): boolean {
  if (!commandMentionsProtectedMemory(command)) return false;
  return (
    /(^|[\s|;&(])(?:rm|mv|cp|touch|mkdir|rmdir|tee|truncate|sed|perl|python|node|ruby|bash|sh|zsh)\b/i.test(command) ||
    /(^|[^<])>>?/.test(command)
  );
}

export function protectedMemoryError(pathLabel = "memory state"): string {
  return `memory mode blocks access to ${pathLabel}`;
}
