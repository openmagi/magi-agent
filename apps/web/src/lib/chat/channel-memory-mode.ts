import type { Channel, ChannelMemoryMode } from "./types";

export type ChannelMemoryModeOption = ChannelMemoryMode;

const MODE_LABELS: Record<Exclude<ChannelMemoryModeOption, "normal">, string> = {
  read_only: "Read-only memory",
  incognito: "No memory",
};

function normalizeText(value: string | null | undefined): string {
  return (value ?? "")
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function detectMemoryModeText(
  value: string | null | undefined,
): Exclude<ChannelMemoryModeOption, "normal"> | null {
  const text = normalizeText(value);
  if (!text) return null;

  if (/\b(?:no memory|memory off|memory disabled|disabled memory)\b/.test(text)) {
    return "incognito";
  }
  if (/\b(?:read only memory|readonly memory|memory read only)\b/.test(text)) {
    return "read_only";
  }
  return null;
}

function sanitizeChannelName(value: string): string {
  return value
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

function appendSlugSuffix(slug: string, suffix: string): string {
  if (slug === suffix || slug.endsWith(`-${suffix}`) || slug.startsWith(`${suffix}-`)) {
    return slug;
  }
  return `${slug}-${suffix}`;
}

function hasLabel(text: string, mode: Exclude<ChannelMemoryModeOption, "normal">): boolean {
  return detectMemoryModeText(text) === mode;
}

export function formatChannelMemoryLabel(
  mode: ChannelMemoryModeOption | null | undefined,
): string | null {
  if (!mode || mode === "normal") return null;
  return MODE_LABELS[mode];
}

export function getChannelMemoryMode(
  channel: Pick<Channel, "name" | "display_name" | "category" | "memory_mode">,
): Exclude<ChannelMemoryModeOption, "normal"> | null {
  if (channel.memory_mode && channel.memory_mode !== "normal") return channel.memory_mode;
  return (
    detectMemoryModeText(channel.name) ??
    detectMemoryModeText(channel.display_name) ??
    detectMemoryModeText(channel.category)
  );
}

export function withChannelMemoryModeSuffix(
  channel: Pick<Channel, "name" | "display_name" | "category" | "memory_mode">,
): string {
  const base = channel.display_name || channel.name;
  const mode = getChannelMemoryMode(channel);
  if (!mode) return base;
  if (hasLabel(base, mode)) return base;
  return `${base} · ${MODE_LABELS[mode]}`;
}

export function buildMemoryModeChannelIdentity(
  rawName: string,
  mode: ChannelMemoryModeOption = "normal",
): { name: string; displayName?: string; memoryMode: ChannelMemoryModeOption } {
  const trimmed = rawName.trim();
  const baseSlug = sanitizeChannelName(trimmed) || `ch-${Date.now().toString(36)}`;
  const inferredMode = mode === "normal" ? (detectMemoryModeText(trimmed) ?? "normal") : mode;

  if (inferredMode === "incognito") {
    const name = appendSlugSuffix(baseSlug, "no-memory");
    return {
      name,
      displayName: hasLabel(trimmed, "incognito")
        ? trimmed
        : `${trimmed || name} · ${MODE_LABELS.incognito}`,
      memoryMode: "incognito",
    };
  }

  if (inferredMode === "read_only") {
    const name = appendSlugSuffix(baseSlug, "read-only-memory");
    return {
      name,
      displayName: hasLabel(trimmed, "read_only")
        ? trimmed
        : `${trimmed || name} · ${MODE_LABELS.read_only}`,
      memoryMode: "read_only",
    };
  }

  const normalizedInputSlug = sanitizeChannelName(trimmed);
  return {
    name: baseSlug,
    ...(baseSlug !== normalizedInputSlug ? { displayName: trimmed || baseSlug } : {}),
    memoryMode: "normal",
  };
}
