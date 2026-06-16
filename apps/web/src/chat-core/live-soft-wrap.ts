/**
 * The live (streaming) transcript renders plain `whitespace-pre-wrap` text for
 * performance instead of re-parsing markdown on every token. Some runtimes
 * stream a turn's reasoning one token per line (a stray `\n` after each chunk),
 * which `whitespace-pre-wrap` faithfully shows as one word — or one Hangul
 * syllable — per line. The finalized message renders markdown, where a lone
 * newline is a soft break (collapses to a space), so it looks clean.
 *
 * This normalizes the live plain-text view to match that soft-break behavior:
 * lone newlines (and any inline whitespace around them) collapse to a single
 * space, while blank lines between paragraphs are preserved.
 *
 * Implemented without regex lookbehind/lookahead so it parses on older Safari
 * (lookbehind is unsupported before 16.4): split on paragraph breaks (a run of
 * newlines, tolerating inline whitespace and CRLF on the blank lines), collapse
 * the lone newlines inside each paragraph, then rejoin.
 */
const PARAGRAPH_BREAK = /\r?\n(?:[^\S\n\r]*\r?\n)+/g;
const LONE_NEWLINE = /[^\S\n\r]*\r?\n[^\S\n\r]*/g;

export function collapseLiveSoftWraps(text: string): string {
  if (!text || !/[\n\r]/.test(text)) return text;
  return text
    .split(PARAGRAPH_BREAK)
    .map((segment) => segment.replace(LONE_NEWLINE, " "))
    .join("\n\n");
}
