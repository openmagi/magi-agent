import type { ChannelState, ChatMessage } from "./types";

export const HISTORY_CACHE_TTL_MS = 60_000;
export const SERVER_MESSAGE_CACHE_TTL_MS = 5_000;
export const SNAPSHOT_CACHE_TTL_MS = 1_500;
export const BACKGROUND_RUNTIME_CHANNEL_LIMIT = 8;

type MessagesByChannel = Record<string, ChatMessage[] | undefined>;
type ChannelStatesByName = Record<string, ChannelState | undefined>;

interface CachedChannelDisplayInput {
  channel: string;
  localMessages: MessagesByChannel;
  serverMessages: MessagesByChannel;
  channelStates: ChannelStatesByName;
}

interface CachedChannelFreshnessInput extends CachedChannelDisplayInput {
  historyLoadedAt: Record<string, number | undefined>;
  now?: number;
  ttlMs?: number;
}

function hasActiveSubagent(channelState: ChannelState | undefined): boolean {
  return (channelState?.subagents ?? []).some(
    (subagent) => subagent.status === "running" || subagent.status === "waiting",
  );
}

function hasRuntimeDisplay(channelState: ChannelState | undefined): boolean {
  return channelState?.streaming === true || channelState?.reconnecting === true;
}

function hasRuntimeActivity(channelState: ChannelState | undefined): boolean {
  return hasRuntimeDisplay(channelState) || hasActiveSubagent(channelState);
}

export function hasCachedChannelDisplay(input: CachedChannelDisplayInput): boolean {
  const { channel, localMessages, serverMessages, channelStates } = input;
  return (
    (localMessages[channel]?.length ?? 0) > 0 ||
    (serverMessages[channel]?.length ?? 0) > 0 ||
    hasRuntimeDisplay(channelStates[channel])
  );
}

export function shouldRefreshByTtl(
  lastStartedAt: number | undefined,
  now = Date.now(),
  ttlMs: number,
): boolean {
  return lastStartedAt === undefined || now - lastStartedAt >= ttlMs;
}

export function shouldUseCachedChannelDisplay(input: CachedChannelFreshnessInput): boolean {
  const {
    channel,
    historyLoadedAt,
    now = Date.now(),
    ttlMs = HISTORY_CACHE_TTL_MS,
  } = input;
  if (!hasCachedChannelDisplay(input)) return false;
  return !shouldRefreshByTtl(historyLoadedAt[channel], now, ttlMs);
}

export function activeRuntimeChannelNames(
  channelStates: ChannelStatesByName,
  activeChannel: string,
  limit = BACKGROUND_RUNTIME_CHANNEL_LIMIT,
): string[] {
  const activeNames = Object.entries(channelStates)
    .filter(([, channelState]) => hasRuntimeActivity(channelState))
    .map(([channel]) => channel);

  const ordered = activeNames.includes(activeChannel)
    ? [activeChannel, ...activeNames.filter((channel) => channel !== activeChannel)]
    : activeNames;

  return ordered.slice(0, Math.max(0, limit));
}
