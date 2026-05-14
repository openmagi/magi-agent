/**
 * Layer 3 — Context Overflow Error detection.
 * Identifies 400/413 errors caused by input token overflow so
 * Turn.ts can trigger emergency inline compaction and retry.
 */

const OVERFLOW_PATTERNS = [
  /prompt is too long/i,
  /max_tokens_exceeded/i,
  /context_length_exceeded/i,
  /request entity too large/i,
  /input.*too (long|large)/i,
  /exceeds.*context/i,
  /maximum context length/i,
];

export function isContextOverflowError(code: string, message: string): boolean {
  if (code !== "http_400" && code !== "http_413") return false;
  if (code === "http_413") return true;
  return OVERFLOW_PATTERNS.some((re) => re.test(message));
}

export class ContextOverflowError extends Error {
  readonly httpCode: string;
  readonly upstreamMessage: string;

  constructor(httpCode: string, upstreamMessage: string) {
    super(`Context overflow (${httpCode}): ${upstreamMessage}`);
    this.name = "ContextOverflowError";
    this.httpCode = httpCode;
    this.upstreamMessage = upstreamMessage;
  }
}
