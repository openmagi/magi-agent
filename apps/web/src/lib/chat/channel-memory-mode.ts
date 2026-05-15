import type { Channel, ChannelMemoryMode } from "./types";

const MEMORY_MODE_LABELS: Partial<Record<ChannelMemoryMode, string>> = {
  read_only: "Read-only memory",
  incognito: "No memory",
};

const MEMORY_MODE_BADGE_LABELS: Partial<Record<ChannelMemoryMode, string>> = {
  read_only: "Read-only",
  incognito: "No mem",
};

const MEMORY_MODE_SUFFIX_PATTERN = /\s+[·-]\s+(?:read-only memory|read only memory|no memory)$/i;

export function getChannelMemoryMode(channel: Pick<Channel, "memory_mode"> | null | undefined): ChannelMemoryMode {
  return channel?.memory_mode ?? "normal";
}

export function formatChannelMemoryLabel(mode: ChannelMemoryMode | null | undefined): string | null {
  return MEMORY_MODE_LABELS[mode ?? "normal"] ?? null;
}

export function formatChannelMemoryBadgeLabel(mode: ChannelMemoryMode | null | undefined): string | null {
  return MEMORY_MODE_BADGE_LABELS[mode ?? "normal"] ?? null;
}

export function stripChannelMemoryModeSuffix(label: string): string {
  const stripped = label.trim().replace(MEMORY_MODE_SUFFIX_PATTERN, "").trim();
  return stripped.length > 0 ? stripped : label.trim();
}

export function formatChannelBaseLabel(
  channel: Pick<Channel, "name" | "display_name">,
): string {
  return stripChannelMemoryModeSuffix(channel.display_name?.trim() || channel.name);
}

export function withChannelMemoryModeSuffix(
  channel: Pick<Channel, "name" | "display_name" | "memory_mode">,
): string {
  const baseLabel = formatChannelBaseLabel(channel);
  const modeLabel = formatChannelMemoryLabel(getChannelMemoryMode(channel));
  return modeLabel ? `${baseLabel} · ${modeLabel}` : baseLabel;
}
