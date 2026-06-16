import type { ChatMessage } from "./types";
import { stripResearchEvidenceMarker } from "./research-evidence";
import { stripAssistantMetadataPreamble } from "./visible-content";

export const ASSISTANT_DEDUPE_WINDOW_MS = 5 * 60_000;
export const ASSISTANT_DEDUPE_MIN_CHARS = 80;

const ATTACHMENT_MARKER_RE = /\[attachment:([0-9a-f-]{36}):([^\]]+)\]/gi;
const REPLACEMENT_CHAR_RE = /\uFFFD+/g;
const MIN_REPLACEMENT_CHUNK_CHARS = 8;

function normalizedAssistantVisibleContent(message: ChatMessage): string | null {
  if (message.role !== "assistant") return null;
  const content = stripResearchEvidenceMarker(stripAssistantMetadataPreamble(message.content));
  const normalized = content.replace(/\s+/g, " ").trim();
  return normalized.length > 0 ? normalized : null;
}

export function normalizedAssistantDedupeContent(message: ChatMessage): string | null {
  const normalized = normalizedAssistantVisibleContent(message);
  if (!normalized) return null;
  return normalized.length >= ASSISTANT_DEDUPE_MIN_CHARS ? normalized : null;
}

function normalizedAssistantArtifactContent(message: ChatMessage): string | null {
  if (message.role !== "assistant") return null;
  const content = stripResearchEvidenceMarker(stripAssistantMetadataPreamble(message.content))
    .replace(ATTACHMENT_MARKER_RE, (_match, _id: string, filename: string) => (
      `[attachment:${filename.trim().toLowerCase()}]`
    ));
  const normalized = content.replace(/\s+/g, " ").trim();
  return normalized.length > 0 ? normalized : null;
}

function attachmentFilenameKeys(message: ChatMessage): string[] {
  const keys: string[] = [];
  const re = new RegExp(ATTACHMENT_MARKER_RE.source, ATTACHMENT_MARKER_RE.flags);
  let match: RegExpExecArray | null;
  while ((match = re.exec(message.content)) !== null) {
    const filename = match[2]?.trim().toLowerCase();
    if (filename) keys.push(`attachment:${filename}`);
  }
  return keys;
}

function evidenceSourceKeys(message: ChatMessage): string[] {
  return (message.researchEvidence?.inspectedSources ?? [])
    .map((source) => source.uri.trim().toLowerCase())
    .filter((uri) => uri.length > 0)
    .map((uri) => `source:${uri}`);
}

function artifactReferenceKeys(message: ChatMessage): Set<string> {
  return new Set([
    ...attachmentFilenameKeys(message),
    ...evidenceSourceKeys(message),
  ]);
}

function shareArtifactReference(first: ChatMessage, second: ChatMessage): boolean {
  const firstKeys = artifactReferenceKeys(first);
  if (firstKeys.size === 0) return false;
  for (const key of artifactReferenceKeys(second)) {
    if (firstKeys.has(key)) return true;
  }
  return false;
}

export function assistantMessagesExactlyMatch(
  first: ChatMessage,
  second: ChatMessage,
): boolean {
  const firstContent = normalizedAssistantVisibleContent(first);
  const secondContent = normalizedAssistantVisibleContent(second);
  return !!firstContent && firstContent === secondContent;
}

function commonPrefixLength(a: string, b: string): number {
  const limit = Math.min(a.length, b.length);
  let index = 0;
  while (index < limit && a[index] === b[index]) index += 1;
  return index;
}

function replacementAwareSequenceMatches(source: string, target: string): boolean {
  if (!source.includes("\uFFFD")) return false;
  const chunks = source
    .split(REPLACEMENT_CHAR_RE)
    .map((chunk) => chunk.trim())
    .filter((chunk) => chunk.length >= MIN_REPLACEMENT_CHUNK_CHARS);
  if (chunks.length === 0) return false;

  let cursor = 0;
  let matchedChars = 0;
  for (const chunk of chunks) {
    const index = target.indexOf(chunk, cursor);
    if (index < 0) return false;
    cursor = index + chunk.length;
    matchedChars += chunk.length;
  }

  const concreteLength = source.replace(REPLACEMENT_CHAR_RE, "").length;
  return concreteLength >= ASSISTANT_DEDUPE_MIN_CHARS && matchedChars / concreteLength >= 0.65;
}

export function assistantContentsSubstantiallyOverlap(
  first: ChatMessage,
  second: ChatMessage,
): boolean {
  const firstContent = normalizedAssistantDedupeContent(first);
  const secondContent = normalizedAssistantDedupeContent(second);
  if (!firstContent || !secondContent) return false;

  if (firstContent === secondContent) return true;
  const shorter = firstContent.length <= secondContent.length ? firstContent : secondContent;
  const longer = firstContent.length > secondContent.length ? firstContent : secondContent;
  if (longer.includes(shorter)) return true;
  if (
    replacementAwareSequenceMatches(firstContent, secondContent) ||
    replacementAwareSequenceMatches(secondContent, firstContent)
  ) {
    return true;
  }

  const sharedPrefix = commonPrefixLength(firstContent, secondContent);
  const shorterLength = Math.min(firstContent.length, secondContent.length);
  return sharedPrefix >= 120 && sharedPrefix / shorterLength >= 0.72;
}

export function assistantArtifactCopiesSubstantiallyOverlap(
  first: ChatMessage,
  second: ChatMessage,
): boolean {
  if (first.role !== "assistant" || second.role !== "assistant") return false;
  if (!shareArtifactReference(first, second)) return false;
  const firstContent = normalizedAssistantArtifactContent(first);
  const secondContent = normalizedAssistantArtifactContent(second);
  if (!firstContent || !secondContent) return false;
  if (firstContent === secondContent) return true;
  const shorter = firstContent.length <= secondContent.length ? firstContent : secondContent;
  const longer = firstContent.length > secondContent.length ? firstContent : secondContent;
  return longer.includes(shorter);
}

export function assistantMessagesSubstantiallyOverlap(
  first: ChatMessage,
  second: ChatMessage,
  windowMs = ASSISTANT_DEDUPE_WINDOW_MS,
): boolean {
  if (first.role !== "assistant" || second.role !== "assistant") return false;
  const firstTs = first.timestamp ?? 0;
  const secondTs = second.timestamp ?? 0;
  if (Math.abs(secondTs - firstTs) >= windowMs) return false;
  return assistantContentsSubstantiallyOverlap(first, second);
}

export function shouldMergeAssistantMessageCopies(
  existing: ChatMessage,
  incoming: ChatMessage,
): boolean {
  if (existing.role !== "assistant" || incoming.role !== "assistant") return false;
  if (existing.serverId && incoming.serverId) return false;
  return assistantMessagesSubstantiallyOverlap(existing, incoming);
}

function replacementCharCount(value: string): number {
  return value.match(REPLACEMENT_CHAR_RE)?.join("").length ?? 0;
}

function preferredAssistantContent(existing: ChatMessage, incoming: ChatMessage): string {
  const existingReplacementCount = replacementCharCount(existing.content);
  const incomingReplacementCount = replacementCharCount(incoming.content);
  if (existingReplacementCount !== incomingReplacementCount) {
    return existingReplacementCount < incomingReplacementCount ? existing.content : incoming.content;
  }

  const existingLength = normalizedAssistantDedupeContent(existing)?.length ?? existing.content.length;
  const incomingLength = normalizedAssistantDedupeContent(incoming)?.length ?? incoming.content.length;
  return incomingLength >= existingLength ? incoming.content : existing.content;
}

export function mergeAssistantMessageCopies(
  existing: ChatMessage,
  incoming: ChatMessage,
): ChatMessage {
  return {
    ...existing,
    ...incoming,
    id: existing.id,
    timestamp: existing.timestamp ?? incoming.timestamp,
    serverId: existing.serverId ?? incoming.serverId,
    content: preferredAssistantContent(existing, incoming),
  };
}

export function shouldPreferIncomingAssistantMessageCopy(
  existing: ChatMessage,
  incoming: ChatMessage,
): boolean {
  const preferredContent = preferredAssistantContent(existing, incoming);
  if (preferredContent === incoming.content && preferredContent !== existing.content) return true;
  if (preferredContent === existing.content && preferredContent !== incoming.content) return false;
  if (!existing.serverId && incoming.serverId) return true;
  if (existing.serverId && !incoming.serverId) return false;
  return (incoming.timestamp ?? 0) >= (existing.timestamp ?? 0);
}
