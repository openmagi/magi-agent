import type { ChannelMemoryMode } from "../util/types.js";

export type ChannelMemoryPolicy = "read_only" | "disabled";

function appChannelFromSessionKey(sessionKey: string): string | null {
  const parts = sessionKey.split(":");
  if (parts.length < 4) return null;
  if (parts[2] !== "app") return null;
  return parts[3] || null;
}

function normalizeText(value: string): string {
  return value
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function channelMemoryPolicyFromChannelName(
  channelName: string,
): ChannelMemoryPolicy | null {
  const normalized = normalizeText(channelName);
  if (!normalized) return null;
  if (/\b(?:no memory|memory off|memory disabled|disabled memory)\b/.test(normalized)) {
    return "disabled";
  }
  if (/\b(?:read only memory|readonly memory|memory read only)\b/.test(normalized)) {
    return "read_only";
  }
  return null;
}

export function channelMemoryPolicyFromSessionKey(
  sessionKey: string,
): ChannelMemoryPolicy | null {
  const channelName = appChannelFromSessionKey(sessionKey);
  return channelName ? channelMemoryPolicyFromChannelName(channelName) : null;
}

export function memoryModeFromChannelPolicy(
  policy: ChannelMemoryPolicy | null,
): ChannelMemoryMode | undefined {
  if (policy === "read_only") return "read_only";
  if (policy === "disabled") return "incognito";
  return undefined;
}

export function memoryModeFromSessionKey(sessionKey: string): ChannelMemoryMode | undefined {
  return memoryModeFromChannelPolicy(channelMemoryPolicyFromSessionKey(sessionKey));
}

export function shouldSkipMemoryWriteForSession(sessionKey: string): boolean {
  return channelMemoryPolicyFromSessionKey(sessionKey) !== null;
}
