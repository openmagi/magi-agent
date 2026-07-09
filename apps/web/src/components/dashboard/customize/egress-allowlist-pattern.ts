/**
 * U4b -- egress allowlist pattern validity (pure, no React).
 *
 * A client-side mirror of the backend `_valid_allowlist_pattern` grammar
 * (design 5.5): an exact host (`api.github.com`) or a single-suffix wildcard
 * (`*.github.com`, which does NOT match the bare apex). This is a UX affordance
 * only; the backend re-validates and is the source of truth. Kept in its own
 * React-free module so it is unit-testable without a DOM/React runtime.
 */

// RFC-1123 label: 1-63 chars, alphanumeric with internal hyphens, no leading
// or trailing hyphen. A hostname is one or more such labels joined by dots.
const LABEL_RE = /^(?!-)[a-z0-9-]{1,63}(?<!-)$/;
const MAX_HOSTNAME_LEN = 253;

export function isValidAllowlistPattern(raw: string): boolean {
  const token = raw.trim().toLowerCase();
  if (!token) return false;
  const host = token.startsWith("*.") ? token.slice(2) : token;
  if (!host || host.length > MAX_HOSTNAME_LEN) return false;
  return host.split(".").every((label) => LABEL_RE.test(label));
}
