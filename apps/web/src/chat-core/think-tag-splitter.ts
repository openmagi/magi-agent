/**
 * Streaming splitter for inline reasoning tags.
 *
 * Some providers (e.g. Kimi K2.x via Fireworks) emit chain-of-thought inline in
 * the assistant `content` wrapped in `<think>...</think>` rather than on a
 * separate reasoning channel. The markdown renderer strips the tags, so the raw
 * reasoning leaks into the visible answer. This splitter peels `<think>` spans
 * out of the visible stream and routes them to the (collapsible) thinking
 * channel instead, matching the legacy TS-runtime frontend behavior.
 *
 * It is stateful across chunks (tags may straddle SSE delta boundaries) and a
 * safe no-op for content without tags.
 */

const OPEN_TAG = "<think>";
const CLOSE_TAG = "</think>";

export interface ThinkTagSplitterHandlers {
  onVisible: (text: string) => void;
  onThinking: (text: string) => void;
}

export interface ThinkTagSplitter {
  push(text: string): void;
  flush(): void;
}

/**
 * Longest suffix of `buf` that is a proper prefix of `tag` (case-insensitive).
 * Used to hold back a partial tag that may complete in the next chunk.
 */
function trailingTagPrefixLen(buf: string, tag: string): number {
  const max = Math.min(tag.length - 1, buf.length);
  const bufLower = buf.toLowerCase();
  const tagLower = tag.toLowerCase();
  for (let len = max; len >= 1; len--) {
    if (bufLower.slice(buf.length - len) === tagLower.slice(0, len)) return len;
  }
  return 0;
}

export function createThinkTagSplitter(
  handlers: ThinkTagSplitterHandlers,
): ThinkTagSplitter {
  let inside = false;
  let hold = "";

  const emit = (segment: string): void => {
    if (!segment) return;
    if (inside) handlers.onThinking(segment);
    else handlers.onVisible(segment);
  };

  return {
    push(text: string): void {
      if (!text) return;
      let buf = hold + text;
      hold = "";
      for (;;) {
        const tag = inside ? CLOSE_TAG : OPEN_TAG;
        const idx = buf.toLowerCase().indexOf(tag);
        if (idx === -1) {
          const held = trailingTagPrefixLen(buf, tag);
          if (held > 0) {
            emit(buf.slice(0, buf.length - held));
            hold = buf.slice(buf.length - held);
          } else {
            emit(buf);
          }
          return;
        }
        emit(buf.slice(0, idx));
        inside = !inside;
        buf = buf.slice(idx + tag.length);
        if (!buf) return;
      }
    },
    flush(): void {
      if (hold) {
        emit(hold);
        hold = "";
      }
    },
  };
}
