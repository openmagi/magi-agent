/**
 * Graduated fuzzy edit matching + lazy comment detector.
 *
 * P1: 4-stage pipeline (exact → cherry-pick → line-diff → flexible)
 *     × 4 preprocessing variants = up to 16 attempts.
 * P3: LAZY_COMMENT_PATTERNS blocks placeholder comments in new_string.
 */

// ── Types ────────────────────────────────────────────────────

export interface FuzzyMatchResult {
  kind: "exact" | "fuzzy" | "not_found";
  stage?: "cherry_pick" | "line_diff" | "flexible_similarity";
  variant?: "raw" | "blank_stripped" | "relative_indented" | "both";
  similarity?: number;
  offset?: number;
  matchedText?: string;
}

interface Candidate {
  offset: number;
  matchLength: number;
  similarity: number;
}

export interface LazyCommentDetection {
  pattern: string;
  matchedText: string;
  line: number;
}

// ── Main entry ───────────────────────────────────────────────

export function fuzzyFindOldString(
  content: string,
  oldString: string,
  opts?: { replaceAll?: boolean },
): FuzzyMatchResult {
  // Stage 1: exact
  const exact = content.indexOf(oldString);
  if (exact >= 0) return { kind: "exact", offset: exact };

  const threshold = opts?.replaceAll ? 0.50 : 0.95;

  type StageName = "cherry_pick" | "line_diff" | "flexible_similarity";
  type VariantName = "raw" | "blank_stripped" | "relative_indented" | "both";
  type StageFn = (c: string, o: string, t: number) => Candidate[];
  type VariantFn = (s: string) => string;

  const stages: Array<[StageName, StageFn]> = [
    ["cherry_pick", cherryPickDiff],
    ["line_diff", lineDiff],
    ["flexible_similarity", flexibleSimilarity],
  ];

  const variants: Array<[VariantName, VariantFn]> = [
    ["raw", identity],
    ["blank_stripped", blankStripped],
    ["relative_indented", relativeIndented],
    ["both", (s: string) => relativeIndented(blankStripped(s))],
  ];

  for (const [stageName, stageFn] of stages) {
    for (const [variantName, variantFn] of variants) {
      const preparedContent = variantFn(content);
      const preparedOld = variantFn(oldString);

      const candidates = stageFn(preparedContent, preparedOld, threshold);

      if (candidates.length === 1) {
        const candidate = candidates[0]!;
        const originalOffset = mapBackToOriginal(
          candidate.offset,
          preparedContent,
          content,
        );
        const matchedText = extractMatchedText(
          content,
          originalOffset,
          oldString,
        );
        return {
          kind: "fuzzy",
          stage: stageName,
          variant: variantName,
          similarity: candidate.similarity,
          offset: originalOffset,
          matchedText,
        };
      }
    }
  }

  return { kind: "not_found" };
}

// ── Preprocessing variants ───────────────────────────────────

function identity(s: string): string {
  return s;
}

function blankStripped(s: string): string {
  const lines = s.split("\n");
  return lines.filter((line) => line.trim() !== "").join("\n");
}

function relativeIndented(s: string): string {
  const lines = s.split("\n");
  return lines.map((line) => line.trimStart()).join("\n");
}

// ── Stage 2: Cherry-pick diff ────────────────────────────────

function cherryPickDiff(
  content: string,
  oldString: string,
  _threshold: number,
): Candidate[] {
  const contentLines = content.split("\n");
  const oldLines = oldString.split("\n");
  if (oldLines.length === 0) return [];
  const candidates: Candidate[] = [];

  for (let i = 0; i <= contentLines.length - oldLines.length; i++) {
    let matched = 0;
    for (let j = 0; j < oldLines.length; j++) {
      if (contentLines[i + j]!.trim() === oldLines[j]!.trim()) matched++;
    }
    if (matched === oldLines.length) {
      candidates.push({
        offset: charOffsetOfLine(contentLines, i),
        matchLength: charLengthOfLines(contentLines, i, i + oldLines.length),
        similarity: 1.0,
      });
    }
  }
  return candidates;
}

// ── Stage 3: Line-diff (Levenshtein on line arrays) ──────────

function lineDiff(
  content: string,
  oldString: string,
  threshold: number,
): Candidate[] {
  const contentLines = content.split("\n");
  const oldLines = oldString.split("\n");
  const windowSize = oldLines.length;
  if (windowSize === 0 || contentLines.length < windowSize) return [];
  const candidates: Candidate[] = [];

  for (let i = 0; i <= contentLines.length - windowSize; i++) {
    const window = contentLines.slice(i, i + windowSize);
    const distance = levenshteinLines(window, oldLines);
    const maxLen = Math.max(window.length, oldLines.length);
    const similarity = 1 - distance / maxLen;

    if (similarity >= threshold) {
      candidates.push({
        offset: charOffsetOfLine(contentLines, i),
        matchLength: charLengthOfLines(contentLines, i, i + windowSize),
        similarity,
      });
    }
  }
  return deduplicateOverlapping(candidates);
}

// ── Stage 4: Flexible similarity (char-level) ────────────────

function flexibleSimilarity(
  content: string,
  oldString: string,
  threshold: number,
): Candidate[] {
  // Performance guard — skip char-level sliding window on large content
  if (content.length > 50_000 || oldString.length > 5_000) return [];

  const candidates: Candidate[] = [];
  const baseLen = oldString.length;
  const minWindow = Math.floor(baseLen * 0.8);
  const maxWindow = Math.ceil(baseLen * 1.2);

  // Line-aligned windows for performance
  const contentLines = content.split("\n");
  const oldLineCount = oldString.split("\n").length;
  const windowLineMin = Math.max(1, oldLineCount - 2);
  const windowLineMax = oldLineCount + 2;

  for (
    let wLines = windowLineMin;
    wLines <= windowLineMax && wLines <= contentLines.length;
    wLines++
  ) {
    for (let i = 0; i <= contentLines.length - wLines; i++) {
      const windowText = contentLines.slice(i, i + wLines).join("\n");
      if (
        windowText.length < minWindow ||
        windowText.length > maxWindow
      ) {
        continue;
      }
      const sim = normalizedLevenshtein(windowText, oldString);
      if (sim >= threshold) {
        candidates.push({
          offset: charOffsetOfLine(contentLines, i),
          matchLength: windowText.length,
          similarity: sim,
        });
      }
    }
  }

  return deduplicateOverlapping(candidates);
}

// ── Levenshtein ──────────────────────────────────────────────

function levenshteinLines(a: string[], b: string[]): number {
  const m = a.length;
  const n = b.length;
  const dp: number[] = Array.from({ length: n + 1 }, (_, i) => i);

  for (let i = 1; i <= m; i++) {
    let prev = dp[0]!;
    dp[0] = i;
    for (let j = 1; j <= n; j++) {
      const tmp = dp[j]!;
      dp[j] =
        a[i - 1]!.trim() === b[j - 1]!.trim()
          ? prev
          : 1 + Math.min(dp[j - 1]!, dp[j]!, prev);
      prev = tmp;
    }
  }
  return dp[n]!;
}

function normalizedLevenshtein(a: string, b: string): number {
  const maxLen = Math.max(a.length, b.length);
  if (maxLen === 0) return 1;
  const dist = levenshteinChars(a, b);
  return 1 - dist / maxLen;
}

function levenshteinChars(a: string, b: string): number {
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;

  const dp: number[] = Array.from({ length: n + 1 }, (_, i) => i);

  for (let i = 1; i <= m; i++) {
    let prev = dp[0]!;
    dp[0] = i;
    for (let j = 1; j <= n; j++) {
      const tmp = dp[j]!;
      dp[j] =
        a[i - 1] === b[j - 1]
          ? prev
          : 1 + Math.min(dp[j - 1]!, dp[j]!, prev);
      prev = tmp;
    }
  }
  return dp[n]!;
}

// ── Helpers ──────────────────────────────────────────────────

function charOffsetOfLine(lines: string[], lineIndex: number): number {
  let sum = 0;
  for (let i = 0; i < lineIndex && i < lines.length; i++) {
    sum += lines[i]!.length + 1; // +1 for \n
  }
  return sum;
}

function charLengthOfLines(lines: string[], from: number, to: number): number {
  let sum = 0;
  const end = Math.min(to, lines.length);
  for (let i = from; i < end; i++) {
    sum += lines[i]!.length;
    if (i < end - 1) sum += 1; // \n between lines
  }
  return sum;
}

function extractMatchedText(
  content: string,
  offset: number,
  oldString: string,
): string {
  // Find the same number of lines from content at offset
  const oldLineCount = oldString.split("\n").length;
  const contentLines = content.split("\n");

  // Find which line `offset` falls on
  let charCount = 0;
  let startLine = 0;
  for (let i = 0; i < contentLines.length; i++) {
    if (charCount >= offset) {
      startLine = i;
      break;
    }
    charCount += contentLines[i]!.length + 1;
    if (charCount > offset) {
      startLine = i;
      break;
    }
  }

  const endLine = Math.min(startLine + oldLineCount, contentLines.length);
  return contentLines.slice(startLine, endLine).join("\n");
}

function mapBackToOriginal(
  preparedOffset: number,
  preparedContent: string,
  originalContent: string,
): number {
  // Count lines up to preparedOffset in prepared content
  const preparedLines = preparedContent.split("\n");
  let charCount = 0;
  let lineIndex = 0;
  for (let i = 0; i < preparedLines.length; i++) {
    if (charCount >= preparedOffset) {
      lineIndex = i;
      break;
    }
    charCount += preparedLines[i]!.length + 1;
    if (charCount > preparedOffset) {
      lineIndex = i;
      break;
    }
  }

  // Map to same line index in original
  const originalLines = originalContent.split("\n");
  let originalOffset = 0;
  for (let i = 0; i < lineIndex && i < originalLines.length; i++) {
    originalOffset += originalLines[i]!.length + 1;
  }
  return originalOffset;
}

function deduplicateOverlapping(candidates: Candidate[]): Candidate[] {
  if (candidates.length <= 1) return candidates;

  candidates.sort((a, b) => a.offset - b.offset);
  const result: Candidate[] = [candidates[0]!];

  for (let i = 1; i < candidates.length; i++) {
    const prev = result[result.length - 1]!;
    const curr = candidates[i]!;
    if (curr.offset < prev.offset + prev.matchLength) {
      // Overlapping — keep higher similarity
      if (curr.similarity > prev.similarity) {
        result[result.length - 1] = curr;
      }
    } else {
      result.push(curr);
    }
  }
  return result;
}

// ── P3: Lazy Comment Detector ────────────────────────────────

const LAZY_COMMENT_PATTERNS = [
  // JS/TS single-line
  /\/\/\s*\.{3}\s*(?:existing|rest|remaining|other|more|same|previous)\b/i,
  /\/\/\s*\.{3}\s*(?:code|implementation|logic|content|methods|functions)\b/i,
  // JS/TS block comment
  /\/\*\s*\.{3}\s*(?:same as|unchanged|omitted|truncated|abbreviated)\b/i,
  // Python / Shell
  /#\s*\.{3}\s*(?:existing|rest|remaining|other|more|same|previous)\b/i,
  /#\s*\.{3}\s*(?:code|implementation|logic|content)\b/i,
  // Generic bare ellipsis in comment at end of line
  /(?:\/\/|#|\/\*)\s*\.{3}\s*$/m,
  // JSX comment
  /\{\/\*\s*\.{3}.*\*\/\}/,
];

const STRING_LITERAL_RE = /^[^"'`]*(?:["'`]).*(?:["'`])/;

export function detectLazyComments(text: string): LazyCommentDetection | null {
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    // Skip if the match is inside a string literal
    if (isInsideStringLiteral(line)) continue;

    for (const pattern of LAZY_COMMENT_PATTERNS) {
      const match = pattern.exec(line);
      if (match) {
        return {
          pattern: pattern.source,
          matchedText: match[0],
          line: i + 1,
        };
      }
    }
  }
  return null;
}

function isInsideStringLiteral(line: string): boolean {
  // Heuristic: if the line starts with something like `const x = "...`
  // and the comment pattern appears after a quote, skip it.
  // Check if the comment-like content is preceded by an odd number of quotes
  const trimmed = line.trim();

  // Common pattern: `const s = "// ... existing code";`
  for (const quote of ['"', "'", "`"]) {
    const firstQuote = trimmed.indexOf(quote);
    if (firstQuote < 0) continue;
    const lastQuote = trimmed.lastIndexOf(quote);
    if (lastQuote <= firstQuote) continue;

    // Check if `// ...` or `# ...` appears between the quotes
    const between = trimmed.slice(firstQuote + 1, lastQuote);
    for (const pattern of LAZY_COMMENT_PATTERNS) {
      if (pattern.test(between) && !pattern.test(trimmed.slice(0, firstQuote))) {
        return true;
      }
    }
  }
  return false;
}
