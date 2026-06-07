const ROUTE_META_PREAMBLE_RE =
  /^\[META\s*:\s*(?=[^\]]*\b(?:intent|domain|complexity|route)\s*=)[^\]]*\]\s*\n?/i;
const SKILLS_PREAMBLE_RE = /^\[SKILLS\s*:[^\]]*\]\s*\n?/i;
const ROUTE_META_PREFIX_RE = /^\[META\s*:/i;
const INLINE_PROGRESS_LINE_PATTERNS: readonly RegExp[] = [
  /^\d+(?:\.\d+)?s 동안 작업$/i,
  /^\d+(?:\.\d+)?초 동안 작업$/i,
  /^Thinking through next step(?:\s+.+)?$/i,
  /^Calling\s+[a-z0-9_.-]+\/[a-z0-9_.-]+$/i,
  /^Calling\s+[a-z0-9_.-]+$/i,
  /^Still thinking\s+\([^)]+\)$/i,
  /^요청 처리 중(?:\s+\d+(?:\.\d+)?s elapsed)?$/i,
  /^공개 진행 로그를 갱신하고 있습니다$/i,
  /^다음 단계 준비 중(?:\s+\d+(?:\.\d+)?s elapsed)?$/i,
  /^응답 구조 잡는 중(?:\s+\d+(?:\.\d+)?s elapsed)?$/i,
  /^작업 진행 중(?:\s+\d+(?:\.\d+)?s elapsed|\s+\d+초째 작업 중)?$/i,
  /^Organizing files(?:\s+.+)?$/i,
  /^Prepared file(?:\s+\d+(?:\.\d+)?s|\s+\d+ms)?$/i,
  /^Reviewing (?:document|file)(?:\s+.+)?$/i,
  /^Searching the web(?:\s+.+)?$/i,
  /^Subagent (?:running|waiting|completed|failed|cancelled|aborted)(?:\s+.+)?$/i,
  /^Using tools(?:\s+.+)?$/i,
  /^Waiting for tool approval(?:\s+.+)?$/i,
  /^Tool (?:batch completed|permission decided)(?:\s+.+)?$/i,
  /^Model pass(?: done)?\s+\d+(?:\s+.+)?$/i,
  /^자료 (?:읽는|조사하는|검토하는) 중(?:\s+.+)?$/i,
  /^\/bin\/sh:\s*\d+:/i,
];
const INLINE_PROGRESS_DETAIL_RE =
  /^(?:workspace|skills-learned|src|infra|docs|apps|memory|scripts|supabase)\/\S+$/i;
const DUPLICATE_BLOCK_MIN_CHARS = 80;

function normalizeForDuplicateBlock(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function collapseRepeatedAssistantBlock(content: string): string {
  const normalizedContent = normalizeForDuplicateBlock(content);
  if (normalizedContent.length < DUPLICATE_BLOCK_MIN_CHARS * 2) return content;

  const boundaryPattern = /\n{2,}/g;
  let match: RegExpExecArray | null;
  while ((match = boundaryPattern.exec(content)) !== null) {
    const splitAt = match.index + match[0].length;
    const left = content.slice(0, match.index).trim();
    const right = content.slice(splitAt).trim();
    const normalizedLeft = normalizeForDuplicateBlock(left);
    if (normalizedLeft.length < DUPLICATE_BLOCK_MIN_CHARS) continue;
    if (normalizedLeft === normalizeForDuplicateBlock(right)) return left;
  }

  return content;
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
  const lines = content.split(/\r?\n/);
  const kept: string[] = [];
  let droppedAny = false;
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
  return collapseRepeatedAssistantBlock(compacted);
}

export function stripAssistantMetadataPreamble(content: string): string {
  let visible = content;
  if (content.startsWith("[META:")) {
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
