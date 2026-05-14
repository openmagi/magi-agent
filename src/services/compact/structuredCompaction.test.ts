/**
 * Structured Compaction Template tests (P4).
 *
 * Design reference: docs/plans/2026-05-11-context-intelligence.md §P4.
 *
 * Covers:
 *   1. validateCompactionOutput: all 10 headers present → valid
 *   2. validateCompactionOutput: missing header → invalid + correct missing list
 *   3. validateCompactionOutput: partial missing → lists only the missing ones
 *   4. STRUCTURED_COMPACTION_PROMPT contains all 10 required section headers
 *   5. REQUIRED_HEADERS array has exactly 10 entries
 *   6. buildRetryPrompt: includes missing section names
 *   7. summariseStructured: returns valid output on first try (no retry)
 *   8. summariseStructured: retries once when first attempt misses sections
 *   9. summariseStructured: accepts best-effort after max 2 retries
 *  10. summariseStructured: returns null on LLM failure (fail-open)
 *  11. summariseStructured: preserves file paths in output
 *  12. summariseStructured: preserves code snippets in output
 *  13. summariseStructured: preserves numeric values in output
 *  14. Integration: maybeCompact uses structured template prompt
 */

import { describe, it, expect } from "vitest";
import crypto from "node:crypto";
import {
  REQUIRED_HEADERS,
  STRUCTURED_COMPACTION_PROMPT,
  validateCompactionOutput,
  buildRetryPrompt,
} from "./structuredCompaction.js";
import { ContextEngine, type CompactionBoundaryEntry } from "./ContextEngine.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type {
  LLMClient,
  LLMEvent,
  LLMStreamRequest,
} from "../../transport/LLMClient.js";
import type { Session } from "../../Session.js";

// ── Mocks ──────────────────────────────────────────────────────────────

interface MockLLM {
  client: LLMClient;
  calls: LLMStreamRequest[];
}

function mockLLM(
  responder: (req: LLMStreamRequest) => LLMEvent[] | Error,
): MockLLM {
  const calls: LLMStreamRequest[] = [];
  async function* stream(
    req: LLMStreamRequest,
  ): AsyncGenerator<LLMEvent, void, void> {
    calls.push(req);
    const result = responder(req);
    if (result instanceof Error) throw result;
    for (const evt of result) yield evt;
  }
  const client = { stream } as unknown as LLMClient;
  return { client, calls };
}

interface FakeTranscript {
  appended: TranscriptEntry[];
  append: (entry: TranscriptEntry) => Promise<void>;
}

function fakeTranscript(): FakeTranscript {
  const appended: TranscriptEntry[] = [];
  return {
    appended,
    append: async (entry: TranscriptEntry) => {
      appended.push(entry);
    },
  };
}

function fakeSession(
  sessionKey = "agent:main:test:1",
): { session: Session; transcript: FakeTranscript } {
  const transcript = fakeTranscript();
  const session = {
    meta: { sessionKey },
    transcript,
  } as unknown as Session;
  return { session, transcript };
}

function userEntry(turnId: string, text: string, ts = 1_000): TranscriptEntry {
  return { kind: "user_message", ts, turnId, text };
}

// ── Valid compaction output fixture ────────────────────────────────────

function validCompactionOutput(): string {
  return [
    "## 1. Active Intent",
    "Deploy the structured compaction feature to production.",
    "",
    "## 2. Completed Steps",
    "- Wrote failing tests (TDD RED)",
    "- Implemented validateCompactionOutput function",
    "",
    "## 3. Current Plan",
    "1. Implement buildRetryPrompt",
    "2. Wire into ContextEngine.summarise",
    "3. Run tests (TDD GREEN)",
    "",
    "## 4. Modified Files",
    "`src/services/compact/structuredCompaction.ts` — created",
    "`src/services/compact/ContextEngine.ts` — modified",
    "",
    "## 5. Key Code Snippets",
    "```typescript",
    "function validateCompactionOutput(summary: string): CompactionValidation {",
    "  const missing = REQUIRED_HEADERS.filter(h => !summary.includes(h));",
    "  return { valid: missing.length === 0, missing };",
    "}",
    "```",
    "",
    "## 6. Important Values",
    "| Key | Value |",
    "|-----|-------|",
    "| Max retries | 2 |",
    "| Summary model | claude-haiku-4-5 |",
    "| Token budget | 1536 |",
    "",
    "## 7. Decisions Made",
    "Chose 10-section template over 7-section. Rejected free-form compaction.",
    "",
    "## 8. Pending Questions",
    "None.",
    "",
    "## 9. Execution Contract State",
    "No active execution contract.",
    "",
    "## 10. Next Immediate Step",
    "Run vitest to confirm all tests pass.",
  ].join("\n");
}

// ── Unit tests: validation ────────────────────────────────────────────

describe("validateCompactionOutput", () => {
  it("returns valid when all 10 headers are present", () => {
    const result = validateCompactionOutput(validCompactionOutput());
    expect(result.valid).toBe(true);
    expect(result.missing).toEqual([]);
  });

  it("returns invalid with correct missing list when a header is absent", () => {
    const output = validCompactionOutput().replace("## 4. Modified Files", "");
    const result = validateCompactionOutput(output);
    expect(result.valid).toBe(false);
    expect(result.missing).toEqual(["## 4. Modified Files"]);
  });

  it("lists only the actually missing headers", () => {
    let output = validCompactionOutput();
    output = output.replace("## 5. Key Code Snippets", "");
    output = output.replace("## 9. Execution Contract State", "");
    const result = validateCompactionOutput(output);
    expect(result.valid).toBe(false);
    expect(result.missing).toHaveLength(2);
    expect(result.missing).toContain("## 5. Key Code Snippets");
    expect(result.missing).toContain("## 9. Execution Contract State");
  });
});

// ── Unit tests: prompt & constants ────────────────────────────────────

describe("STRUCTURED_COMPACTION_PROMPT", () => {
  it("contains all 10 required section headers", () => {
    for (const header of REQUIRED_HEADERS) {
      expect(STRUCTURED_COMPACTION_PROMPT).toContain(header);
    }
  });
});

describe("REQUIRED_HEADERS", () => {
  it("has exactly 10 entries", () => {
    expect(REQUIRED_HEADERS).toHaveLength(10);
  });
});

// ── Unit tests: retry prompt ──────────────────────────────────────────

describe("buildRetryPrompt", () => {
  it("includes the names of missing sections", () => {
    const missing = ["## 4. Modified Files", "## 6. Important Values"];
    const prompt = buildRetryPrompt(missing);
    expect(prompt).toContain("## 4. Modified Files");
    expect(prompt).toContain("## 6. Important Values");
  });
});

// ── Integration: summariseStructured via ContextEngine ─────────────────

describe("ContextEngine structured compaction", () => {
  it("uses structured template prompt when compacting", async () => {
    const { client, calls } = mockLLM(() => [
      { kind: "text_delta", blockIndex: 0, delta: validCompactionOutput() },
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 50 } },
    ]);
    const engine = new ContextEngine(client);
    const { session } = fakeSession();

    const bigText = "x".repeat(20_000);
    const entries: TranscriptEntry[] = [userEntry("t1", bigText, 1_000)];

    await engine.maybeCompact(session, entries, 1_000);

    expect(calls.length).toBeGreaterThanOrEqual(1);
    const system = String(calls[0]!.system);
    expect(system).toContain("You MUST output ALL 10 sections below");
    expect(system).toContain("## 1. Active Intent");
    expect(system).toContain("## 10. Next Immediate Step");
  });

  it("retries when first attempt misses required sections", async () => {
    const incompleteSummary = validCompactionOutput()
      .replace("## 4. Modified Files\n`src/services/compact/structuredCompaction.ts` — created\n`src/services/compact/ContextEngine.ts` — modified\n\n", "");

    let callCount = 0;
    const { client, calls } = mockLLM(() => {
      callCount++;
      if (callCount === 1) {
        return [
          { kind: "text_delta", blockIndex: 0, delta: incompleteSummary },
          { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 50 } },
        ];
      }
      return [
        { kind: "text_delta", blockIndex: 0, delta: validCompactionOutput() },
        { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 50 } },
      ];
    });
    const engine = new ContextEngine(client);
    const { session, transcript } = fakeSession();

    const bigText = "x".repeat(20_000);
    const entries: TranscriptEntry[] = [userEntry("t1", bigText, 1_000)];

    const boundary = await engine.maybeCompact(session, entries, 1_000);
    expect(boundary).not.toBeNull();
    expect(calls.length).toBe(2);
    expect(boundary!.summaryText).toContain("## 4. Modified Files");
  });

  it("accepts best-effort after max 2 retries", async () => {
    const incompleteSummary = validCompactionOutput()
      .replace("## 4. Modified Files\n`src/services/compact/structuredCompaction.ts` — created\n`src/services/compact/ContextEngine.ts` — modified\n\n", "");

    const { client, calls } = mockLLM(() => [
      { kind: "text_delta", blockIndex: 0, delta: incompleteSummary },
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 50 } },
    ]);
    const engine = new ContextEngine(client);
    const { session, transcript } = fakeSession();

    const bigText = "x".repeat(20_000);
    const entries: TranscriptEntry[] = [userEntry("t1", bigText, 1_000)];

    const boundary = await engine.maybeCompact(session, entries, 1_000);
    expect(boundary).not.toBeNull();
    // 1 initial + 2 retries = 3 calls max
    expect(calls.length).toBe(3);
    // Still writes a boundary with best-effort output
    expect(transcript.appended.length).toBe(2);
  });

  it("returns null on LLM failure (fail-open preserved)", async () => {
    const { client } = mockLLM(() => new Error("haiku down"));
    const engine = new ContextEngine(client);
    const { session, transcript } = fakeSession();

    const bigText = "x".repeat(20_000);
    const entries: TranscriptEntry[] = [userEntry("t1", bigText, 1_000)];

    const result = await engine.maybeCompact(session, entries, 1_000);
    expect(result).toBeNull();
    expect(transcript.appended.length).toBe(0);
  });

  it("preserves file paths in compaction output", async () => {
    const output = validCompactionOutput();
    const { client } = mockLLM(() => [
      { kind: "text_delta", blockIndex: 0, delta: output },
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 50 } },
    ]);
    const engine = new ContextEngine(client);
    const { session } = fakeSession();

    const entries: TranscriptEntry[] = [userEntry("t1", "x".repeat(20_000), 1_000)];
    const boundary = await engine.maybeCompact(session, entries, 1_000);

    expect(boundary).not.toBeNull();
    expect(boundary!.summaryText).toContain("src/services/compact/structuredCompaction.ts");
    expect(boundary!.summaryText).toContain("src/services/compact/ContextEngine.ts");
  });

  it("preserves code snippets in compaction output", async () => {
    const output = validCompactionOutput();
    const { client } = mockLLM(() => [
      { kind: "text_delta", blockIndex: 0, delta: output },
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 50 } },
    ]);
    const engine = new ContextEngine(client);
    const { session } = fakeSession();

    const entries: TranscriptEntry[] = [userEntry("t1", "x".repeat(20_000), 1_000)];
    const boundary = await engine.maybeCompact(session, entries, 1_000);

    expect(boundary).not.toBeNull();
    expect(boundary!.summaryText).toContain("validateCompactionOutput");
  });

  it("preserves numeric values in compaction output", async () => {
    const output = validCompactionOutput();
    const { client } = mockLLM(() => [
      { kind: "text_delta", blockIndex: 0, delta: output },
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 50 } },
    ]);
    const engine = new ContextEngine(client);
    const { session } = fakeSession();

    const entries: TranscriptEntry[] = [userEntry("t1", "x".repeat(20_000), 1_000)];
    const boundary = await engine.maybeCompact(session, entries, 1_000);

    expect(boundary).not.toBeNull();
    expect(boundary!.summaryText).toContain("1536");
    expect(boundary!.summaryText).toContain("claude-haiku-4-5");
  });
});
