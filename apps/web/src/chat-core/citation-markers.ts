/**
 * Source-citation display projection (Wave 3b, web UI).
 *
 * The runtime writes canonical ``src_N`` ids into the model-facing tool
 * results, the model cites them inline as ``[src_N]``, and the terminal
 * ``turn_result`` frame carries a ``citations`` payload (see
 * ``CitationsPayload`` in ``./types``). This module is the pure, display-only
 * projection that:
 *
 *   - parses the wire payload defensively (``parseCitationsPayload``),
 *   - builds a lookup from canonical ``src_N`` to its ``[n]`` display index
 *     (``buildCitationIndex``), preferring the terminal payload and falling
 *     back to an OPTIMISTIC first-appearance numbering during streaming, and
 *   - offers a tolerant token scanner so malformed near-misses (``[src3]``,
 *     ``(src_3)``, ``[SRC_3]``) resolve for DISPLAY only, mirroring the Python
 *     ``normalize_citation_token``. Tolerant tokens never invent a canonical
 *     ref: they only map to an id that already exists in the index.
 *
 * Numbering is first-appearance order in BOTH the payload and the optimistic
 * path, so the two agree; a mismatch only occurs on dangling refs, which
 * de-chip on finalize.
 */
import type {
  CitationMarker,
  CitationSourceEntry,
  CitationVerdict,
  CitationsPayload,
  InspectedSourceKind,
} from "./types";

/**
 * Canonical bare ``src_N`` reference. Mirrors the Python
 * ``research_final_gate._FINAL_ANSWER_SOURCE_REF_RE`` verbatim so the web and
 * CLI renderers extract the same markers. Used for first-appearance numbering.
 */
const CANONICAL_REF_RE = /\bsrc_[1-9][0-9]*\b/g;

/**
 * Inline citation token as the model writes it, plus tolerant near-misses.
 * Mirrors the Python ``_TOLERANT_TOKEN_RE`` shape
 * (``[\[(]?\s*src_?([1-9][0-9]*)\s*[\])]?``) but anchored to bracketed /
 * parenthesized inline occurrences so we only rewrite intentional citation
 * tokens inside prose, never bare ``src_3`` mentions.
 */
const INLINE_TOKEN_RE = /[[(]\s*src_?([1-9][0-9]*)\s*[\])]/gi;

const TRUST_TIERS = new Set(["primary", "official", "secondary", "unknown"]);
const SOURCE_KINDS = new Set<InspectedSourceKind>([
  "web_search",
  "web_fetch",
  "browser",
  "kb",
  "file",
  "external_repo",
  "external_doc",
  "subagent_result",
]);
const VERDICTS = new Set<CitationVerdict>([
  "cited",
  "partial",
  "uncited",
  "not_applicable",
]);

function normalizeKind(value: unknown): InspectedSourceKind {
  return typeof value === "string" && SOURCE_KINDS.has(value as InspectedSourceKind)
    ? (value as InspectedSourceKind)
    : "external_doc";
}

function normalizeTrustTier(value: unknown): CitationSourceEntry["trustTier"] {
  return typeof value === "string" && TRUST_TIERS.has(value)
    ? (value as CitationSourceEntry["trustTier"])
    : null;
}

/**
 * Validate the terminal-frame ``citations`` object into a ``CitationsPayload``.
 * Returns ``null`` for anything that is not a well-formed payload so a malformed
 * frame never breaks the transcript (fail-quiet, matching the runtime posture).
 */
export function parseCitationsPayload(raw: unknown): CitationsPayload | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;

  const markers: CitationMarker[] = [];
  if (Array.isArray(obj.markers)) {
    for (const entry of obj.markers) {
      if (!Array.isArray(entry) || entry.length < 2) continue;
      const [sourceId, index] = entry;
      if (typeof sourceId === "string" && typeof index === "number" && Number.isFinite(index)) {
        markers.push([sourceId, index] as CitationMarker);
      }
    }
  }

  const sources: CitationSourceEntry[] = [];
  if (Array.isArray(obj.sources)) {
    for (const entry of obj.sources) {
      if (!entry || typeof entry !== "object") continue;
      const s = entry as Record<string, unknown>;
      if (typeof s.sourceId !== "string" || typeof s.n !== "number") continue;
      sources.push({
        n: s.n,
        sourceId: s.sourceId,
        uri: typeof s.uri === "string" ? s.uri : "",
        title: typeof s.title === "string" ? s.title : null,
        kind: normalizeKind(s.kind),
        trustTier: normalizeTrustTier(s.trustTier),
        inspected: s.inspected === true,
      });
    }
  }

  const danglingRefs: string[] = Array.isArray(obj.danglingRefs)
    ? obj.danglingRefs.filter((v): v is string => typeof v === "string")
    : [];

  const verdict: CitationVerdict = VERDICTS.has(obj.verdict as CitationVerdict)
    ? (obj.verdict as CitationVerdict)
    : "not_applicable";

  // A payload with no markers, no sources, and no dangling refs carries no
  // display information; treat it as absent so callers stay byte-identical.
  if (markers.length === 0 && sources.length === 0 && danglingRefs.length === 0) {
    return null;
  }
  return { markers, sources, danglingRefs, verdict };
}

/**
 * First-appearance ``src_N -> n`` map computed from raw text. Used for the
 * OPTIMISTIC streaming path before the terminal payload arrives. Only canonical
 * ``src_N`` refs participate (mirrors the Python marker extraction).
 */
export function computeOptimisticMarkers(text: string): Map<string, number> {
  const map = new Map<string, number>();
  if (!text) return map;
  const re = new RegExp(CANONICAL_REF_RE.source, "g");
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const id = match[0];
    if (!map.has(id)) map.set(id, map.size + 1);
  }
  return map;
}

export interface CitationIndex {
  /** Number of resolvable ``[n]`` markers. */
  readonly size: number;
  /** Display index for a canonical ``src_N``, or ``null`` when not cited. */
  displayIndexFor(sourceId: string): number | null;
  /** True when the id was cited but is dangling (no source entry). */
  isDangling(sourceId: string): boolean;
}

/**
 * Build the citation lookup for a message. Prefers the terminal ``payload``
 * (final, authoritative mapping incl. dangling refs); falls back to optimistic
 * first-appearance numbering derived from ``text`` while streaming.
 */
export function buildCitationIndex(
  payload: CitationsPayload | null | undefined,
  text: string,
): CitationIndex {
  const map = new Map<string, number>();
  const dangling = new Set<string>();
  if (payload) {
    for (const [sourceId, index] of payload.markers) map.set(sourceId, index);
    for (const id of payload.danglingRefs) dangling.add(id);
  } else {
    for (const [id, n] of computeOptimisticMarkers(text)) map.set(id, n);
  }
  return {
    size: map.size,
    displayIndexFor: (sourceId) => (map.has(sourceId) ? map.get(sourceId)! : null),
    isDangling: (sourceId) => dangling.has(sourceId),
  };
}

/** True when ``text`` contains at least one canonical ``src_N`` reference. */
export function hasCanonicalCitationRef(text: string): boolean {
  return new RegExp(CANONICAL_REF_RE.source).test(text);
}

export type CitationTokenPart =
  | { kind: "text"; value: string }
  | { kind: "marker"; sourceId: string; index: number }
  | { kind: "dangling"; sourceId: string; raw: string };

/**
 * Split a text run into plain text and citation-token parts. Each recognized
 * inline token (canonical or tolerant near-miss) resolves to its canonical
 * ``src_N``; if that id has a display index it becomes a ``marker`` part,
 * otherwise it de-chips to a ``dangling`` part (rendered as its literal text).
 */
export function splitCitationTokens(text: string, index: CitationIndex): CitationTokenPart[] {
  const parts: CitationTokenPart[] = [];
  const re = new RegExp(INLINE_TOKEN_RE.source, "gi");
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) {
      parts.push({ kind: "text", value: text.slice(last, match.index) });
    }
    const sourceId = `src_${match[1]}`;
    const display = index.displayIndexFor(sourceId);
    if (display !== null) {
      parts.push({ kind: "marker", sourceId, index: display });
    } else {
      // Not cited (dangling ref or an unresolved near-miss): keep the literal
      // token as plain text so nothing clickable/attributive is fabricated.
      parts.push({ kind: "dangling", sourceId, raw: match[0] });
    }
    last = re.lastIndex;
  }
  if (last < text.length) parts.push({ kind: "text", value: text.slice(last) });
  return parts;
}

/** Custom-event name for cross-linking transcript chips and the Sources panel. */
export const CITATION_FOCUS_EVENT = "magi:citation-focus";

export interface CitationFocusDetail {
  sourceId: string;
}

export function readCitationFocusEvent(event: Event): CitationFocusDetail | null {
  const detail = (event as CustomEvent).detail as CitationFocusDetail | undefined;
  if (!detail || typeof detail.sourceId !== "string" || !detail.sourceId) return null;
  return { sourceId: detail.sourceId };
}

export function emitCitationFocus(sourceId: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(CITATION_FOCUS_EVENT, { detail: { sourceId } }),
  );
}
