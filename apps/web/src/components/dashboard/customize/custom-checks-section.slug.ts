/**
 * Derive a dashboard-check `id` that is ALWAYS valid against the backend
 * `^[a-z0-9][a-z0-9_-]{0,62}$` (max 63 chars). Mirrors `slug_of` in
 * `magi_agent/packs/dashboard_authored.py`:
 *
 * - lowercase; collapse every run of non-`[a-z0-9]` to a single `-`; strip
 *   leading/trailing `-`.
 * - empty result → `"check"`.
 * - clamp to 63 chars (re-stripping any trailing `-` left by the clamp).
 * - on collision with `takenIds`, append `-2`, `-3`, … until unique.
 *
 * Pure (no React / no `@/` imports) so it is directly unit-testable in the
 * node test environment without dragging in client-only modules.
 */
export function slugifyCheckId(
  label: string,
  takenIds?: ReadonlySet<string>,
): string {
  let base = (label || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (!base) base = "check";
  if (base.length > 63) {
    base = base.slice(0, 63).replace(/-+$/g, "");
    if (!base) base = "check";
  }
  if (!takenIds || !takenIds.has(base)) return base;
  let n = 2;
  while (takenIds.has(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}
