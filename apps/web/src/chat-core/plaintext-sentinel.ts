export const PLAINTEXT_PREFIX = "plaintext:v1:";

export function wrapPlaintext(content: string): string {
  return PLAINTEXT_PREFIX + content;
}

export function isPlaintext(encryptedContent: string): boolean {
  return encryptedContent.startsWith(PLAINTEXT_PREFIX);
}

/**
 * Read/render utility: strips the plaintext sentinel prefix.
 * Returns the input unchanged when it is not a plaintext-wrapped value.
 */
export function unwrapPlaintext(encryptedContent: string): string {
  if (!isPlaintext(encryptedContent)) return encryptedContent;
  return encryptedContent.slice(PLAINTEXT_PREFIX.length);
}
