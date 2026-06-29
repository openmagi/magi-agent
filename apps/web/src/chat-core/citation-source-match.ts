/**
 * Citation URL matcher.
 *
 * Resolves a markdown link's ``href`` to a matching ``InspectedSource``
 * so the UI can render an inline citation chip (PR3 of Track A).
 *
 * Matching is intentionally STRICT: same origin + canonical path + same
 * query.  Differences only in ``www.`` prefix, trailing slash, fragment,
 * or scheme/host case are normalized away.  Anything else → no match,
 * because over-eager matching would make the chip's promise dishonest:
 * "this exact link came from this exact source."
 *
 * No tool-side changes are required.  The model writes
 * ``[label](https://...)``; if that URL was inspected during the turn,
 * the matcher resolves it to the source.
 */
import type { InspectedSource } from "./types";


/**
 * Normalize a citation href into a stable lookup key.
 *
 * Returns ``null`` when the href is not a comparable http(s) URL.  All other
 * cases produce a deterministic string: lowercased scheme + host (with any
 * ``www.`` prefix collapsed), the path with a redundant trailing slash
 * stripped (root ``/`` preserved), the search query verbatim, and no
 * fragment.
 */
export function normalizeCitationHref(href: string): string | null {
  if (typeof href !== "string" || href.length === 0) return null;
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;

  let host = url.host.toLowerCase();
  if (host.startsWith("www.")) host = host.slice(4);

  let path = url.pathname;
  if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);

  return `${url.protocol.toLowerCase()}//${host}${path}${url.search}`;
}


/**
 * Find the ``InspectedSource`` whose ``uri`` resolves to the same canonical
 * form as ``href``.  When multiple sources share the same canonical URI the
 * EARLIEST-inspected one wins so a re-fetched URL keeps the original chip
 * stable across the turn.
 */
export function matchCitationSource(
  href: string,
  sources: readonly InspectedSource[],
): InspectedSource | null {
  if (!href || !Array.isArray(sources) || sources.length === 0) return null;
  const target = normalizeCitationHref(href);
  if (target === null) return null;

  let best: InspectedSource | null = null;
  for (const source of sources) {
    if (!source || typeof source.uri !== "string") continue;
    const candidate = normalizeCitationHref(source.uri);
    if (candidate !== target) continue;
    if (best === null) {
      best = source;
      continue;
    }
    if (source.inspectedAt < best.inspectedAt) {
      best = source;
    }
  }
  return best;
}
