import { createHash } from "node:crypto";

/**
 * Hash a gateway/API token into a searchable prefix + full SHA-256 hash.
 * Tokens shorter than 12 chars are treated as invalid and return empty strings.
 */
export function hashToken(token: string): { prefix: string; hash: string } {
  if (typeof token !== "string" || token.length < 12) {
    return { prefix: "", hash: "" };
  }
  const prefix = token.slice(0, 12);
  const hash = createHash("sha256").update(token, "utf8").digest("hex");
  return { prefix, hash };
}
