import { parseMarkers } from "./attachment-marker";
import { parseKbContextMarker } from "./kb-context-marker";
import { compareChatMessages } from "./message-order";
import type { ChatMessage } from "./types";

export interface ChatExportMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

export interface ChatExportMarkdownInput {
  botName: string;
  channelName: string;
  exportedAt: Date;
  messages: ChatExportMessage[];
}

function stripAttachmentMarkers(content: string): string {
  let cleaned = content;
  for (const marker of parseMarkers(content)) {
    cleaned = cleaned.replace(marker.fullMatch, "");
  }
  return cleaned
    .replace(/\[attachment:[0-9a-f-]{36}:[^\]]+\]/gi, "")
    .replace(/\[Attachment: [^\]]+\]\(attachment:[^)]+\)/gi, "")
    .trim();
}

export function cleanChatExportContent(content: string): string {
  const withoutKbContext = parseKbContextMarker(content).text;
  return stripAttachmentMarkers(withoutKbContext).trim();
}

export function normalizeSelectedChatExportMessages(
  messages: ChatMessage[],
  selectedIds: Set<string>,
): ChatExportMessage[] {
  return messages
    .filter((message) => message.role !== "system")
    .filter(
      (message) =>
        selectedIds.has(message.id) ||
        (typeof message.serverId === "string" && selectedIds.has(message.serverId)),
    )
    .sort(compareChatMessages)
    .map((message) => ({
      id: message.id,
      role: message.role as "user" | "assistant",
      content: cleanChatExportContent(message.content),
      timestamp: message.timestamp,
    }))
    .filter((message) => message.content.trim().length > 0);
}

function formatExportTimestamp(timestamp: number): string {
  const date = new Date(timestamp);
  const year = date.getUTCFullYear();
  const month = `${date.getUTCMonth() + 1}`.padStart(2, "0");
  const day = `${date.getUTCDate()}`.padStart(2, "0");
  const hours = `${date.getUTCHours()}`.padStart(2, "0");
  const minutes = `${date.getUTCMinutes()}`.padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}`;
}

function roleLabel(role: ChatExportMessage["role"]): string {
  return role === "user" ? "User" : "Assistant";
}

export function buildChatExportMarkdown(input: ChatExportMarkdownInput): string {
  const lines = [
    "# Open Magi Chat Export",
    "",
    `- Bot: ${input.botName}`,
    `- Channel: ${input.channelName}`,
    `- Exported: ${input.exportedAt.toISOString()}`,
    `- Messages: ${input.messages.length}`,
    "",
  ];

  for (const message of input.messages) {
    lines.push(`## ${roleLabel(message.role)} - ${formatExportTimestamp(message.timestamp)}`);
    lines.push("");
    lines.push(message.content.trim());
    lines.push("");
  }

  return `${lines.join("\n").trim()}\n`;
}

function slugPart(value: string): string {
  const slug = value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return slug || "chat";
}

export function buildChatExportFilename(input: {
  botName: string;
  channelName: string;
  exportedAt: Date;
}): string {
  const day = input.exportedAt.toISOString().slice(0, 10);
  return `open-magi-${slugPart(input.botName)}-${slugPart(input.channelName)}-${day}.md`;
}
