const LEADING_ROUTE_META_RE = /^\s*\[META\s*:[\s\S]*?\]\s*/i;

export function stripLeadingRouteMetaTag(text: string): string {
  return text.replace(LEADING_ROUTE_META_RE, "");
}
