import type { ChatMessage, ResearchEvidenceSnapshot, ResponseUsage } from "./types";
import { normalizeResearchEvidenceSnapshot } from "./research-evidence";

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

function normalizeResponseUsage(value: unknown): ResponseUsage | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  const inputTokens = record.inputTokens;
  const outputTokens = record.outputTokens;
  const costUsd = record.costUsd;
  if (
    typeof inputTokens !== "number" ||
    typeof outputTokens !== "number" ||
    typeof costUsd !== "number" ||
    !Number.isFinite(inputTokens) ||
    !Number.isFinite(outputTokens) ||
    !Number.isFinite(costUsd)
  ) {
    return undefined;
  }
  return {
    inputTokens: Math.max(0, Math.floor(inputTokens)),
    outputTokens: Math.max(0, Math.floor(outputTokens)),
    costUsd: Math.max(0, costUsd),
  };
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
