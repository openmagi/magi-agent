const ROUTE_META_PREAMBLE_RE =
  /^\[?META\s*:\s*(?=[^\]\n]*\b(?:intent|domain|complexity|route)\s*=)[^\]\n]*\]?[ \t]*\n?/i;
const ROUTE_META_INLINE_RE =
  /\[?META\s*:\s*(?=[^\]\n]*\b(?:intent|domain|complexity|route)\s*=)[^\]\n]*\]?[ \t]*/gi;
const ROUTE_META_FRAGMENT_BEFORE_META_RE = /\[?META\s*:[^\]\n]{0,80}(?=\[?META\s*:)/gi;
const ROUTE_META_TRAILING_FRAGMENT_RE = /\[?META\s*:[^\]\n]{0,80}(?=\n|$)/gi;
const SKILLS_PREAMBLE_RE = /^\[SKILLS\s*:[^\]]*\]\s*\n?/i;
const ROUTE_META_PREFIX_RE = /^\[?META\s*:/i;
const INLINE_PROGRESS_LINE_PATTERNS: readonly RegExp[] = [
  /^\d+(?:\.\d+)?s 동안 작업$/i,
  /^\d+(?:\.\d+)?초 동안 작업$/i,
  /^Thinking through next step(?:\s+.+)?$/i,
  /^Calling\s+[a-z0-9_.-]+\/[a-z0-9_.-]+$/i,
  /^Still thinking\s+\([^)]+\)$/i,
  /^요청 처리 중(?:\s+\d+(?:\.\d+)?s elapsed)?$/i,
  /^공개 진행 로그를 갱신하고 있습니다$/i,
  /^다음 단계 준비 중(?:\s+\d+(?:\.\d+)?s elapsed)?$/i,
  /^응답 구조 잡는 중(?:\s+\d+(?:\.\d+)?s elapsed)?$/i,
  /^작업 진행 중(?:\s+\d+(?:\.\d+)?s elapsed|\s+\d+초째 작업 중)?$/i,
  /^Organizing files(?:\s+.+)?$/i,
  /^Prepared file(?:\s+\d+(?:\.\d+)?s|\s+\d+ms)?$/i,
  /^Reviewing (?:[a-z0-9_.-]+\s+)?(?:document|file)(?:\s+.+)?$/i,
  /^Searching the web(?:\s+.+)?$/i,
  /^Subagent (?:running|waiting|completed|failed|cancelled|aborted)(?:\s+.+)?$/i,
  /^Using tools(?:\s+.+)?$/i,
  /^Waiting for tool approval(?:\s+.+)?$/i,
  /^Tool (?:batch completed|permission decided)(?:\s+.+)?$/i,
  /^Model pass(?: done)?\s+\d+(?:\s+.+)?$/i,
  /^문서 검토(?:\s+.+)?$/i,
  /^자료 (?:읽는|조사하는|검토하는) 중(?:\s+.+)?$/i,
  /^\/bin\/sh:\s*\d+:/i,
];
const INLINE_PROGRESS_DETAIL_RE =
  /^(?:workspace|skills-learned|src|infra|docs|apps|memory|scripts|supabase)\/\S+$/i;
const DUPLICATE_BLOCK_MIN_CHARS = 80;

function normalizeForDuplicateBlock(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function stripInlineRouteMetadata(content: string): string {
  return content
    .replace(ROUTE_META_FRAGMENT_BEFORE_META_RE, "")
    .replace(ROUTE_META_INLINE_RE, "")
    .replace(ROUTE_META_TRAILING_FRAGMENT_RE, "")
    .replace(/[ \t]+\n/g, "\n");
}

function collapseRepeatedAssistantBlock(
  content: string,
  minChars = DUPLICATE_BLOCK_MIN_CHARS,
): string {
  const normalizedContent = normalizeForDuplicateBlock(content);
  if (normalizedContent.length < minChars * 2) return content;

  const boundaryPattern = /\n{2,}/g;
  let match: RegExpExecArray | null;
  while ((match = boundaryPattern.exec(content)) !== null) {
    const splitAt = match.index + match[0].length;
    const left = content.slice(0, match.index).trim();
    const right = content.slice(splitAt).trim();
    const normalizedLeft = normalizeForDuplicateBlock(left);
    if (normalizedLeft.length < minChars) continue;
    if (normalizedLeft === normalizeForDuplicateBlock(right)) return left;
  }

  return content;
}

function collapseAdjacentRepeatedParagraphs(content: string): string {
  const paragraphs = content.split(/\n{2,}/);
  const kept: string[] = [];
  for (const paragraph of paragraphs) {
    const normalized = normalizeForDuplicateBlock(paragraph);
    const previous = kept[kept.length - 1];
    if (previous && normalized && normalized === normalizeForDuplicateBlock(previous)) {
      continue;
    }
    kept.push(paragraph);
  }
  return kept.join("\n\n");
}

function isInlineProgressLine(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.length > 0 && INLINE_PROGRESS_LINE_PATTERNS.some((pattern) => pattern.test(trimmed));
}

function isInlineProgressDetailLine(line: string): boolean {
  const trimmed = line.trim();
  return INLINE_PROGRESS_DETAIL_RE.test(trimmed);
}

function stripAssistantInlineProgress(content: string): string {
  const withoutRouteMetadata = stripInlineRouteMetadata(content);
  const lines = withoutRouteMetadata.split(/\r?\n/);
  const kept: string[] = [];
  let droppedAny = withoutRouteMetadata !== content;
  let previousWasProgress = false;

  for (const line of lines) {
    if (isInlineProgressLine(line)) {
      droppedAny = true;
      previousWasProgress = true;
      continue;
    }
    if (previousWasProgress && isInlineProgressDetailLine(line)) {
      droppedAny = true;
      continue;
    }
    previousWasProgress = false;
    kept.push(line);
  }

  if (!droppedAny) return content;
  const compacted = kept
    .join("\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return collapseRepeatedAssistantBlock(
    collapseAdjacentRepeatedParagraphs(compacted),
    droppedAny ? 24 : DUPLICATE_BLOCK_MIN_CHARS,
  );
}

export function stripAssistantMetadataPreamble(content: string): string {
  let visible = content;
  if (ROUTE_META_PREFIX_RE.test(content)) {
    const withoutMeta = content.replace(ROUTE_META_PREAMBLE_RE, "");
    if (withoutMeta !== content) visible = withoutMeta.replace(SKILLS_PREAMBLE_RE, "");
  }
  return stripAssistantInlineProgress(visible);
}

export function stripStreamingAssistantMetadataPreamble(content: string): string {
  if (ROUTE_META_PREFIX_RE.test(content)) {
    if (!content.includes("]")) return "";
    return stripAssistantMetadataPreamble(content);
  }
  return stripAssistantInlineProgress(content);
}
