import {
  inferRouteMetaLanguage,
  isRouteMetaTag,
  localizeRouteMetaTag,
} from "./routeMeta.js";

const ROUTE_META_TAG_RE = /\[META\s*:\s*(?=[^\]]*\b(?:intent|domain|complexity|route)\s*=)[^\]]*\]\s*/gi;
const ROUTE_META_PREFIX = "[META:";
const META_TAG_ONLY_RE = /^\[META\s*:[^\]]*\]/i;

export function normalizeUserVisibleRouteMetaTags(text: string): string {
  let seenRouteMeta = false;
  const language = inferRouteMetaLanguage(text.replace(ROUTE_META_TAG_RE, " "));
  return text.replace(ROUTE_META_TAG_RE, (match) => {
    if (seenRouteMeta) return "";
    seenRouteMeta = true;
    return localizeMatchedRouteMeta(match, language);
  });
}

export class UserVisibleRouteMetaFilter {
  private buffer = "";
  private seenRouteMeta = false;
  private stripLeadingWhitespaceAfterMeta = false;
  private pendingFirstRouteMeta: string | null = null;

  reset(): void {
    this.buffer = "";
    this.seenRouteMeta = false;
    this.stripLeadingWhitespaceAfterMeta = false;
    this.pendingFirstRouteMeta = null;
  }

  filter(delta: string): string {
    if (delta.length === 0) return "";
    if (this.stripLeadingWhitespaceAfterMeta) {
      delta = delta.replace(/^\s+/, "");
      this.stripLeadingWhitespaceAfterMeta = delta.length === 0;
      if (delta.length === 0) return "";
    }
    this.buffer += delta;
    return this.drain(false);
  }

  flush(): string {
    return this.drain(true);
  }

  private drain(flush: boolean): string {
    let out = "";
    for (;;) {
      if (this.pendingFirstRouteMeta) {
        const language = inferRouteMetaLanguage(this.buffer);
        if (language || flush || this.buffer.trim().length > 0) {
          out += localizeRouteMetaTag(this.pendingFirstRouteMeta, language);
          this.pendingFirstRouteMeta = null;
          continue;
        }
        return out;
      }

      const start = indexOfRouteMetaStart(this.buffer);
      if (start === -1) {
        if (flush) {
          out += this.buffer;
          this.buffer = "";
          return out;
        }
        const keep = trailingRouteMetaPrefixLength(this.buffer);
        const emitLength = this.buffer.length - keep;
        if (emitLength > 0) {
          out += this.buffer.slice(0, emitLength);
          this.buffer = this.buffer.slice(emitLength);
        }
        return out;
      }

      if (start > 0) {
        out += this.buffer.slice(0, start);
        this.buffer = this.buffer.slice(start);
      }

      const end = this.buffer.indexOf("]");
      if (end === -1) {
        if (flush) {
          out += this.buffer;
          this.buffer = "";
        }
        return out;
      }

      const tag = this.buffer.slice(0, end + 1);
      if (isRouteMetaTag(tag)) {
        const rest = this.buffer.slice(end + 1);
        if (!this.seenRouteMeta) {
          this.seenRouteMeta = true;
          this.pendingFirstRouteMeta = tag;
          this.buffer = rest;
          this.stripLeadingWhitespaceAfterMeta = false;
          continue;
        }
        this.buffer = rest.replace(/^\s+/, "");
        this.stripLeadingWhitespaceAfterMeta = this.buffer.length === 0;
        continue;
      }

      out += tag;
      this.buffer = this.buffer.slice(end + 1);
    }
  }
}

function indexOfRouteMetaStart(text: string): number {
  return text.toUpperCase().indexOf("[META");
}

function localizeMatchedRouteMeta(match: string, language: ReturnType<typeof inferRouteMetaLanguage>): string {
  const tag = META_TAG_ONLY_RE.exec(match)?.[0];
  if (!tag) return match;
  return `${localizeRouteMetaTag(tag, language)}${match.slice(tag.length)}`;
}

function trailingRouteMetaPrefixLength(text: string): number {
  const upper = text.toUpperCase();
  const max = Math.min(ROUTE_META_PREFIX.length, upper.length);
  for (let len = max; len > 0; len -= 1) {
    if (ROUTE_META_PREFIX.startsWith(upper.slice(-len))) return len;
  }
  return 0;
}
