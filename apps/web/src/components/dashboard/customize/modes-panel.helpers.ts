// Pure helpers for the Modes panel. Split out (no React / `@/` deps) so the
// unit test runs in the node env — same pattern as `custom-checks-section.slug`.

/** Slug used as the stable mode id when creating a new mode. Matches the
 * backend `_MODE_ID_RE` in `magi_agent/customize/modes.py`
 * (`[a-z0-9][a-z0-9_-]{0,63}`). Falls back to "mode" for empty/non-Latin.
 *
 * When `taken` is supplied, a colliding base id is disambiguated with a
 * `-N` suffix (starting at 2) so creating a new mode never silently overwrites
 * an existing one via the id-keyed upsert. The suffix keeps the result within
 * the 64-char backend cap. */
export function slugifyModeId(displayName: string, taken?: ReadonlySet<string>): string {
  const slug = displayName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
  // Guarantee a leading [a-z0-9] and a non-empty result.
  const base = slug.replace(/^[^a-z0-9]+/, "") || "mode";
  if (!taken || !taken.has(base)) return base;
  for (let n = 2; ; n++) {
    const suffix = `-${n}`;
    const candidate = `${base.slice(0, 64 - suffix.length)}${suffix}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/** Split a freeform textarea (newline- or comma-separated) into trimmed,
 * non-empty entries, de-duplicated in first-seen order. */
export function parseList(text: string): string[] {
  const seen: string[] = [];
  for (const raw of text.split(/[\n,]/)) {
    const entry = raw.trim();
    if (entry.length > 0 && !seen.includes(entry)) seen.push(entry);
  }
  return seen;
}

/** The set of ids currently selected in a scoped-policy list (the mode editor
 * keeps the list as a newline/comma string; the picker reads selection from it). */
export function selectedScopedIds(raw: string): Set<string> {
  return new Set(parseList(raw));
}

/** Toggle one id in a scoped-policy list string. Adds it when absent, removes it
 * when present; every OTHER id (including ones not shown in the picker, e.g. a
 * ``seam_spec:`` or a since-deleted policy) is preserved. Returns the rewritten
 * newline-joined string. */
export function toggleScopedId(raw: string, id: string): string {
  const set = selectedScopedIds(raw);
  if (set.has(id)) set.delete(id);
  else set.add(id);
  return [...set].join("\n");
}
