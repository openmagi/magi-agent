import type { ResponseUsage, ServerMessage } from "./types";

function recordFromUnknown(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function normalizeResponseUsage(value: unknown): ResponseUsage | undefined {
  const record = recordFromUnknown(value);
  if (!record) return undefined;
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
  if (inputTokens === 0 && outputTokens === 0 && costUsd === 0) return undefined;
  if (inputTokens === 0 && outputTokens > 0 && costUsd === 0) return undefined;
  return {
    inputTokens: Math.max(0, Math.floor(inputTokens)),
    outputTokens: Math.max(0, Math.floor(outputTokens)),
    costUsd: Math.max(0, costUsd),
  };
}

export function responseUsageFromServerMessage(
  message: Pick<ServerMessage, "usage">,
): ResponseUsage | undefined {
  return normalizeResponseUsage(message.usage);
}

const RESPONSE_USAGE_MARKER_RE =
  /\n?\s*<!-- clawy:response-usage:v1:[A-Za-z0-9_-]+ -->\s*$/;

export function stripResponseUsageMarker(content: string): string {
  return content.replace(RESPONSE_USAGE_MARKER_RE, "");
}
