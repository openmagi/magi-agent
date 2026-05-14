const ROUTE_META_PREAMBLE_RE =
  /^\[META\s*:\s*(?=[^\]]*\b(?:intent|domain|complexity|route)\s*=)[^\]]*\]\s*\n?/i;
const SKILLS_PREAMBLE_RE = /^\[SKILLS\s*:[^\]]*\]\s*\n?/i;

export function stripAssistantMetadataPreamble(content: string): string {
  if (!content.startsWith("[META:")) return content;
  const withoutMeta = content.replace(ROUTE_META_PREAMBLE_RE, "");
  if (withoutMeta === content) return content;
  return withoutMeta.replace(SKILLS_PREAMBLE_RE, "");
}
