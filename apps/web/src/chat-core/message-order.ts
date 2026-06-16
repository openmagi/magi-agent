import type { ChatMessage } from "./types";

const ROLE_ORDER: Record<ChatMessage["role"], number> = {
  user: 0,
  assistant: 1,
  system: 2,
};

const CLIENT_MESSAGE_TIME_RE = /^(?:user|assistant|injected|queued)-(\d{10,})/;

function clientMessageTime(message: ChatMessage): number | null {
  const candidate = message.id ?? message.serverId;
  if (!candidate) return null;
  const match = candidate.match(CLIENT_MESSAGE_TIME_RE);
  if (!match) return null;
  const parsed = Number.parseInt(match[1]!, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

export function compareChatMessages(a: ChatMessage, b: ChatMessage): number {
  const diff = (clientMessageTime(a) ?? a.timestamp ?? 0) - (clientMessageTime(b) ?? b.timestamp ?? 0);
  if (diff !== 0) return diff;

  const roleDiff = ROLE_ORDER[a.role] - ROLE_ORDER[b.role];
  if (roleDiff !== 0) return roleDiff;

  return (a.serverId ?? a.id ?? a.content).localeCompare(b.serverId ?? b.id ?? b.content);
}
