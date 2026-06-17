import type { ChatMessage, ResearchEvidenceSnapshot, ResponseUsage } from "./types";
import { normalizeResearchEvidenceSnapshot } from "./research-evidence";
import { normalizeResponseUsage } from "./response-usage";

export interface HistoryPlaintextInput {
  role: "user" | "assistant";
  content: string;
  thinkingContent?: string;
  thinkingDuration?: number;
  researchEvidence?: ResearchEvidenceSnapshot;
  usage?: ResponseUsage;
}

export interface DecodedHistoryPlaintext {
  content: string;
  thinkingContent?: string;
  thinkingDuration?: number;
  researchEvidence?: ResearchEvidenceSnapshot;
  usage?: ResponseUsage;
}

interface AssistantHistoryEnvelope {
  _v: number;
  content?: unknown;
  thinking?: unknown;
  thinkingDuration?: unknown;
  researchEvidence?: unknown;
  usage?: unknown;
}

export function encodeHistoryPlaintext(message: HistoryPlaintextInput): string {
  if (message.role !== "assistant") return message.content;
  if (!message.thinkingContent && !message.researchEvidence && !message.usage) return message.content;
  const envelope = {
    _v: message.researchEvidence || message.usage ? 3 : 2,
    content: message.content,
    ...(message.thinkingContent ? { thinking: message.thinkingContent } : {}),
    ...(typeof message.thinkingDuration === "number"
      ? { thinkingDuration: message.thinkingDuration }
      : {}),
    ...(message.researchEvidence ? { researchEvidence: message.researchEvidence } : {}),
    ...(message.usage ? { usage: message.usage } : {}),
  };
  return JSON.stringify(envelope);
}

export function decodeHistoryPlaintext(
  role: ChatMessage["role"],
  raw: string,
): DecodedHistoryPlaintext {
  if (role !== "assistant" || !raw.startsWith('{"_v":')) {
    return { content: raw };
  }

  try {
    const envelope = JSON.parse(raw) as AssistantHistoryEnvelope;
    if (envelope._v !== 2 && envelope._v !== 3) return { content: raw };
    if (typeof envelope.content !== "string") return { content: raw };
    const thinkingContent = typeof envelope.thinking === "string" ? envelope.thinking : undefined;
    const thinkingDuration =
      typeof envelope.thinkingDuration === "number" && Number.isFinite(envelope.thinkingDuration)
        ? envelope.thinkingDuration
        : undefined;
    const researchEvidence = envelope._v >= 3
      ? normalizeResearchEvidenceSnapshot(envelope.researchEvidence)
      : undefined;
    const usage = envelope._v >= 3 ? normalizeResponseUsage(envelope.usage) : undefined;
    return {
      content: envelope.content,
      ...(thinkingContent ? { thinkingContent } : {}),
      ...(thinkingDuration !== undefined ? { thinkingDuration } : {}),
      ...(researchEvidence ? { researchEvidence } : {}),
      ...(usage ? { usage } : {}),
    };
  } catch {
    return { content: raw };
  }
}
