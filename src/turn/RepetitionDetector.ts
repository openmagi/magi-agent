/**
 * RepetitionDetector — detects LLM text degeneration during streaming.
 *
 * When a model enters a degenerate state, it repeats the same phrase
 * or sentence indefinitely within a single response. This detector
 * accumulates streamed text and checks for repeated substrings using
 * a suffix-based approach: it looks for the longest suffix of the
 * accumulated text that also appears earlier, and fires when that
 * repeated pattern occurs >= threshold times.
 *
 * Design decisions:
 *   - MIN_PATTERN_LEN = 40 chars: avoids false positives on short
 *     phrases like "네", "확인", "감사합니다" that may legitimately
 *     repeat 3+ times.
 *   - REPEAT_THRESHOLD = 3: catches degeneration early (3 full repeats)
 *     before it becomes a wall of text.
 *   - CHECK_INTERVAL = 200 chars: doesn't run on every tiny delta,
 *     only when enough text has accumulated since the last check.
 */

export interface RepetitionDetectorConfig {
  /** Minimum length of a pattern to be considered (default: 40). */
  minPatternLen?: number;
  /** Number of times a pattern must repeat to trigger (default: 3). */
  repeatThreshold?: number;
  /** Only run detection every N accumulated chars (default: 200). */
  checkInterval?: number;
}

export interface RepetitionResult {
  detected: boolean;
  pattern?: string;
  count?: number;
}

export class RepetitionDetector {
  private text = "";
  private lastCheckLen = 0;
  private readonly minPatternLen: number;
  private readonly repeatThreshold: number;
  private readonly checkInterval: number;

  constructor(config: RepetitionDetectorConfig = {}) {
    this.minPatternLen = config.minPatternLen ?? 40;
    this.repeatThreshold = config.repeatThreshold ?? 3;
    this.checkInterval = config.checkInterval ?? 200;
  }

  /** Feed a new text delta. Returns detection result. */
  feed(delta: string): RepetitionResult {
    this.text += delta;

    // Only check periodically to avoid O(n²) on every tiny chunk.
    if (this.text.length - this.lastCheckLen < this.checkInterval) {
      return { detected: false };
    }
    this.lastCheckLen = this.text.length;

    return this.check();
  }

  /** Force a check regardless of interval. */
  check(): RepetitionResult {
    const text = this.text;
    if (text.length < this.minPatternLen * this.repeatThreshold) {
      return { detected: false };
    }

    // Strategy: try candidate patterns from the tail of the text.
    // Start with longer candidates (more specific = fewer false positives).
    // The longest repeated suffix is the strongest signal.

    // Try pattern lengths from ~1/3 of text down to minPatternLen.
    const maxPatternLen = Math.min(
      Math.floor(text.length / this.repeatThreshold),
      500, // Cap search to avoid O(n²) on very long text
    );

    // Coarse scan (step 10) for long patterns, then fine scan (step 1)
    // near minPatternLen to catch exact-length sentence repetitions.
    const candidates: number[] = [];
    for (let p = maxPatternLen; p >= this.minPatternLen + 10; p -= 10) {
      candidates.push(p);
    }
    // Fine-grained scan for the last 10 steps to catch exact lengths.
    for (let p = Math.min(maxPatternLen, this.minPatternLen + 9); p >= this.minPatternLen; p--) {
      candidates.push(p);
    }

    for (const patLen of candidates) {
      // Extract candidate from the tail of accumulated text.
      const candidate = text.slice(-patLen);

      // Count non-overlapping occurrences in the full text.
      let count = 0;
      let searchFrom = 0;
      while (searchFrom <= text.length - patLen) {
        const idx = text.indexOf(candidate, searchFrom);
        if (idx === -1) break;
        count++;
        if (count >= this.repeatThreshold) {
          return { detected: true, pattern: candidate.slice(0, 80), count };
        }
        searchFrom = idx + patLen;
      }
    }

    // Also try sentence-level detection: split on sentence boundaries
    // and look for repeated sentences.
    return this.checkSentenceRepetition(text);
  }

  private checkSentenceRepetition(text: string): RepetitionResult {
    // Split on common sentence-ending patterns (Korean + English).
    const sentences = text.split(/(?<=[.!?。！？\n])\s*/).filter(
      (s) => s.length >= this.minPatternLen,
    );

    if (sentences.length < this.repeatThreshold) {
      return { detected: false };
    }

    // Count occurrences of each sentence.
    const counts = new Map<string, number>();
    for (const s of sentences) {
      // Normalize whitespace for comparison.
      const normalized = s.replace(/\s+/g, " ").trim();
      counts.set(normalized, (counts.get(normalized) ?? 0) + 1);
    }

    for (const [sentence, count] of counts) {
      if (count >= this.repeatThreshold && sentence.length >= this.minPatternLen) {
        return { detected: true, pattern: sentence.slice(0, 80), count };
      }
    }

    return { detected: false };
  }

  /** Get the accumulated text (useful for truncation after detection). */
  getText(): string {
    return this.text;
  }

  /** Reset the detector state. */
  reset(): void {
    this.text = "";
    this.lastCheckLen = 0;
  }
}
