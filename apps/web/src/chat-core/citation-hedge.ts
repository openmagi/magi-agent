/**
 * Deterministic sentinel that tags the source-citation fail-open hedge notice
 * (GAP #5). Emitted by the runtime as the first line of a markdown blockquote:
 *
 *   > [!citation-hedge]
 *   > Contains unverified figures; no source was available for: ...
 *
 * Kept byte-identical to the backend
 * `magi_agent/evidence/citation_gate.py::CITATION_HEDGE_SENTINEL`. GitHub-alert
 * admonition syntax the answer body never emits on its own, so detection cannot
 * false-positive on normal prose.
 */
export const CITATION_HEDGE_SENTINEL = "[!citation-hedge]";

/**
 * Result of stripping the hedge sentinel from a blockquote's plain text.
 * `isHedge` is true only when the sentinel is the leading token; `body` is the
 * remaining hedge text with the sentinel removed.
 */
export interface CitationHedgeMatch {
  isHedge: boolean;
  body: string;
}

/**
 * Detect and strip the hedge sentinel from a blockquote's aggregated plain text.
 *
 * Matches ONLY when the sentinel is the first non-whitespace token, so it never
 * fires on normal answer text that merely mentions the phrase later. Returns the
 * body with the sentinel line removed, trimmed of the leading blank line.
 */
export function matchCitationHedge(text: string): CitationHedgeMatch {
  const trimmed = text.replace(/^\s+/, "");
  if (!trimmed.startsWith(CITATION_HEDGE_SENTINEL)) {
    return { isHedge: false, body: text };
  }
  const body = trimmed.slice(CITATION_HEDGE_SENTINEL.length).replace(/^\s+/, "");
  return { isHedge: true, body };
}
