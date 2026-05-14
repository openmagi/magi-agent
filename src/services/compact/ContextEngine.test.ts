/**
 * ContextEngine tests (T1-02).
 *
 * Covers the six cases required by docs/plans/2026-04-19-core-agent-phase-3-plan.md §3 T1-02:
 *   1. No boundary → entries replay as-is.
 *   2. One boundary → pre-boundary collapsed to synthetic summary, post-boundary normal.
 *   3. Two boundaries → latest summary wins; earlier boundary + its post entries dropped.
 *   4. maybeCompact under threshold → no boundary written, returns null.
 *   5. maybeCompact over threshold with working Haiku → boundary created with sha256 hash.
 *   6. maybeCompact with failing Haiku → fail open, returns null, no boundary.
 */

import { describe, it, expect } from "vitest";
import crypto from "node:crypto";
import {
  ContextEngine,
  CompactionImpossibleError,
  RESERVE_TOKEN_CAP_FRACTION,
  type CompactionBoundaryEntry,
} from "./ContextEngine.js";
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
  // Only `stream` is used by ContextEngine — cast is intentional.
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

// ── Fixtures ───────────────────────────────────────────────────────────

function userEntry(turnId: string, text: string, ts = 1_000): TranscriptEntry {
  return { kind: "user_message", ts, turnId, text };
}

function assistantEntry(
  turnId: string,
  text: string,
  ts = 2_000,
): TranscriptEntry {
  return { kind: "assistant_text", ts, turnId, text };
}

function toolCallEntry(
  turnId: string,
  toolUseId: string,
  name: string,
  input: unknown = {},
  ts = 3_000,
): TranscriptEntry {
  return { kind: "tool_call", ts, turnId, toolUseId, name, input };
}

function toolResultEntry(
  turnId: string,
  toolUseId: string,
  output = "result",
  ts = 4_000,
): TranscriptEntry {
  return { kind: "tool_result", ts, turnId, toolUseId, status: "ok", output };
}

function committedEntry(turnId: string, ts = 9_000): TranscriptEntry {
  return { kind: "turn_committed", ts, turnId, inputTokens: 100, outputTokens: 50 };
}

function canonicalAssistantEntry(
  turnId: string,
  content: unknown[],
  ts = 3_000,
): TranscriptEntry {
  return {
    kind: "canonical_message",
    ts,
    turnId,
    messageId: `${turnId}:assistant:1`,
    role: "assistant",
    content,
  };
}

function boundaryEntry(
  boundaryId: string,
  summaryText: string,
  ts: number,
): CompactionBoundaryEntry {
  return {
    kind: "compaction_boundary",
    ts,
    turnId: "agent:main:test:1",
    boundaryId,
    beforeTokenCount: 10_000,
    afterTokenCount: 200,
    summaryHash: crypto.createHash("sha256").update(summaryText).digest("hex"),
    summaryText,
    createdAt: ts,
  };
}

// ── Tests ──────────────────────────────────────────────────────────────

describe("ContextEngine.buildMessagesFromTranscript", () => {
  it("replays all entries as-is when no compaction boundary exists", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      userEntry("t1", "hello", 1_000),
      assistantEntry("t1", "world", 2_000),
      userEntry("t2", "again", 3_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    expect(messages.length).toBe(3);
    expect(messages[0]?.role).toBe("user");
    expect(messages[0]?.content).toContain("hello");
    expect(messages[1]?.role).toBe("assistant");
    expect(messages[2]?.content).toContain("again");
  });

  it("prefers canonical assistant messages over legacy assistant/tool entries for the same turn", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      userEntry("t1", "inspect state", 1_000),
      {
        kind: "canonical_message",
        ts: 1_500,
        turnId: "t1",
        messageId: "t1:assistant:1",
        role: "assistant",
        content: [
          { type: "thinking", thinking: "private reasoning", signature: "sig" },
          { type: "text", text: "I will inspect it." },
          { type: "tool_use", id: "tu_1", name: "FileRead", input: { path: "state.json" } },
        ],
      },
      assistantEntry("t1", "legacy duplicate text", 2_000),
      toolCallEntry("t1", "tu_1", "FileRead", { path: "state.json" }, 3_000),
      toolResultEntry("t1", "tu_1", "{\"ok\":true}", 4_000),
      committedEntry("t1", 5_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    expect(messages).toHaveLength(3);
    expect(messages[1]?.role).toBe("assistant");
    const assistantBlocks = messages[1]?.content as Array<{ type: string; text?: string }>;
    expect(assistantBlocks.map((block) => block.type)).toEqual(["text", "tool_use"]);
    expect(JSON.stringify(assistantBlocks)).not.toContain("private reasoning");
    expect(JSON.stringify(assistantBlocks)).not.toContain("legacy duplicate text");
    expect(messages[2]?.role).toBe("user");
  });

  it("collapses pre-boundary entries into a single synthetic summary message", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      userEntry("t1", "old-user", 1_000),
      assistantEntry("t1", "old-assistant", 2_000),
      boundaryEntry("01HB1", "SUMMARY-OF-T1", 3_000),
      userEntry("t2", "new-user", 4_000),
      assistantEntry("t2", "new-assistant", 5_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    expect(messages.length).toBe(3);
    // [0] is the synthetic summary.
    const summary = messages[0];
    expect(summary).toBeDefined();
    const summaryContent = Array.isArray(summary!.content)
      ? summary!.content.map((b) => (b.type === "text" ? b.text : "")).join("")
      : summary!.content;
    expect(summaryContent).toContain("[Compaction boundary 01HB1");
    expect(summaryContent).toContain("SUMMARY-OF-T1");
    // [1] + [2] are post-boundary entries in order.
    expect(messages[1]?.content).toContain("new-user");
    expect(messages[2]?.role).toBe("assistant");
  });

  it("collapses to only the latest boundary summary when multiple boundaries exist", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      userEntry("t1", "oldest-user", 1_000),
      boundaryEntry("01HB1", "SUMMARY-1", 2_000),
      userEntry("t2", "between-user", 3_000),
      assistantEntry("t2", "between-assistant", 4_000),
      boundaryEntry("01HB2", "SUMMARY-2", 5_000),
      userEntry("t3", "latest-user", 6_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    expect(messages.length).toBe(2);
    const summaryContent = Array.isArray(messages[0]!.content)
      ? messages[0]!.content.map((b) => (b.type === "text" ? b.text : "")).join("")
      : (messages[0]!.content as string);
    expect(summaryContent).toContain("01HB2");
    expect(summaryContent).toContain("SUMMARY-2");
    // Earlier summary is dropped (it was absorbed into SUMMARY-2's context).
    expect(summaryContent).not.toContain("SUMMARY-1");
    expect(messages[1]?.content).toContain("latest-user");
  });
});

describe("ContextEngine.maybeCompact", () => {
  const structuredSummary = [
    "## 1. Active Intent",
    "Continue the current task.",
    "## 2. Completed Steps",
    "- Inspected transcript.",
    "## 3. Current Plan",
    "1. Resume from handoff.",
    "## 4. Modified Files",
    "None.",
    "## 5. Key Code Snippets",
    "None.",
    "## 6. Important Values",
    "None.",
    "## 7. Decisions Made",
    "None.",
    "## 8. Pending Questions",
    "None.",
    "## 9. Execution Contract State",
    "No active execution contract.",
    "## 10. Next Immediate Step",
    "Continue from the latest user request.",
  ].join("\n");

  it("does not compact below the token threshold", async () => {
    const { client, calls } = mockLLM(() => [
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 0, outputTokens: 0 } },
    ]);
    const engine = new ContextEngine(client);
    const { session, transcript } = fakeSession();

    const entries: TranscriptEntry[] = [
      userEntry("t1", "short message", 1_000),
    ];

    const result = await engine.maybeCompact(session, entries, /*tokenLimit*/ 1_000_000);
    expect(result).toBeNull();
    expect(transcript.appended.length).toBe(0);
    expect(calls.length).toBe(0); // no Haiku call when under threshold
  });

  it("creates a boundary with sha256 hash when Haiku succeeds", async () => {
    const summaryPayload = structuredSummary;
    const { client, calls } = mockLLM(() => [
      { kind: "text_delta", blockIndex: 0, delta: summaryPayload },
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 20 } },
    ]);
    const engine = new ContextEngine(client);
    const { session, transcript } = fakeSession();

    // 20_000-char user message ≈ 5_000 tokens at 4 chars/token heuristic.
    const bigText = "x".repeat(20_000);
    const entries: TranscriptEntry[] = [userEntry("t1", bigText, 1_000)];

    const boundary = await engine.maybeCompact(session, entries, /*tokenLimit*/ 1_000);
    expect(boundary).not.toBeNull();
    expect(boundary!.kind).toBe("compaction_boundary");
    expect(boundary!.summaryText).toBe(summaryPayload);
    expect(boundary!.summaryHash).toBe(
      crypto.createHash("sha256").update(summaryPayload).digest("hex"),
    );
    expect(boundary!.beforeTokenCount).toBeGreaterThanOrEqual(1_000);
    expect(boundary!.afterTokenCount).toBeLessThan(boundary!.beforeTokenCount);
    expect(transcript.appended.length).toBe(2);
    expect(transcript.appended[0]).toMatchObject({
      kind: "canonical_message",
      role: "system",
    });
    expect(transcript.appended[1]).toBe(boundary);
    expect(calls.length).toBe(1);
    expect(calls[0]!.system).toContain("You MUST output ALL 10 sections");
    expect(calls[0]!.system).toContain("## 9. Execution Contract State");
  });

  it("prompts the summarizer to write a next-session handoff memo", async () => {
    const { client, calls } = mockLLM(() => [
      { kind: "text_delta", blockIndex: 0, delta: structuredSummary },
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 20 } },
    ]);
    const engine = new ContextEngine(client);
    const { session } = fakeSession();

    const entries: TranscriptEntry[] = [
      userEntry("t1", "continue the deploy train", 1_000),
      assistantEntry("t1", "investigated blockers", 2_000),
    ];

    await engine.maybeCompact(session, entries, /*tokenLimit*/ 1);

    expect(calls.length).toBe(1);
    const system = String(calls[0]!.system);
    expect(system).toContain("structured handoff memo");
    expect(system).toContain("## 1. Active Intent");
    expect(system).toContain("## 2. Completed Steps");
    expect(system).toContain("## 10. Next Immediate Step");
  });

  it("keeps recent transcript tail visible to the summarizer when input is over budget", async () => {
    const { client, calls } = mockLLM(() => [
      { kind: "text_delta", blockIndex: 0, delta: "summary with tail" },
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 20 } },
    ]);
    const engine = new ContextEngine(client);
    const { session } = fakeSession();

    const entries: TranscriptEntry[] = [
      userEntry("t1", "HEAD_MARKER original objective is deploy compaction patch", 1_000),
      assistantEntry("t1", "middle ".repeat(40_000), 2_000),
      userEntry("t2", "TAIL_MARKER remaining next step is open draft PR", 3_000),
    ];

    await engine.maybeCompact(session, entries, /*tokenLimit*/ 1);

    const payload = String(calls[0]!.messages[0]!.content);
    expect(payload).toContain("HEAD_MARKER");
    expect(payload).toContain("TAIL_MARKER");
    expect(payload).toContain("chars omitted from the middle");
  });

  it("fails open (returns null, no boundary) when Haiku errors out", async () => {
    const { client } = mockLLM(() => new Error("haiku upstream down"));
    const engine = new ContextEngine(client);
    const { session, transcript } = fakeSession();

    const bigText = "y".repeat(20_000);
    const entries: TranscriptEntry[] = [userEntry("t1", bigText, 1_000)];

    const result = await engine.maybeCompact(session, entries, /*tokenLimit*/ 1_000);
    expect(result).toBeNull();
    expect(transcript.appended.length).toBe(0);
  });
});

// ── Gap §11.6 — reserve-token floor capped to model context window ─────

describe("ContextEngine §11.6 reserve-token floor (model-aware)", () => {
  it("caps reserveTokens to RESERVE_TOKEN_CAP_FRACTION × contextWindow on a 16k model", () => {
    const { client } = mockLLM(() => []);
    // Caller requests a 40k reserve (sized for 200k-window Sonnet/Opus),
    // but the router hands the turn to a hypothetical 16k-window model.
    // The engine must cap the reserve at 20% × 16k = 3.2k, not honour
    // the configured 40k (which would exceed the whole window).
    const engine = new ContextEngine(client, {
      reserveTokens: 40_000,
      contextWindowResolver: (m) => (m === "tiny-16k" ? 16_000 : 200_000),
    });

    const effective = engine.effectiveReserveTokens("tiny-16k");
    expect(effective).toBe(Math.floor(16_000 * RESERVE_TOKEN_CAP_FRACTION));
    expect(effective).toBe(3_200);
    expect(effective).toBeLessThan(40_000);

    // Sanity: on a normal 200k model the configured 40k wins (40k <
    // 20% × 200k = 40k — ties go to the configured value via `min`).
    expect(engine.effectiveReserveTokens("big-200k")).toBe(40_000);
  });

  it("compacts successfully when transcript overflows a 16k window that still clears the min-viable budget", async () => {
    const summaryPayload = "mid";
    const { client } = mockLLM(() => [
      { kind: "text_delta", blockIndex: 0, delta: summaryPayload },
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 20 } },
    ]);
    const engine = new ContextEngine(client, {
      // With a 16k window, reserve is capped to 3_200 and minViable
      // defaults to 5_000. Headroom = 16_000 − 3_200 = 12_800 ≥ 5_000
      // so the pre-flight passes and the boundary is written.
      reserveTokens: 40_000,
      minViableBudgetTokens: 5_000,
      contextWindowResolver: () => 16_000,
    });
    const { session, transcript } = fakeSession();

    // tokenLimit is the MessageBuilder-style "start compacting" threshold
    // (≈ 75% of window). We pass a transcript well over it to force the
    // compaction path.
    const tokenLimit = Math.floor(16_000 * 0.75); // 12_000
    const bigText = "z".repeat(60_000); // ~15k tokens at 4 chars/tok
    const entries: TranscriptEntry[] = [userEntry("t1", bigText, 1_000)];

    const boundary = await engine.maybeCompact(
      session,
      entries,
      tokenLimit,
      "tiny-16k",
    );
    expect(boundary).not.toBeNull();
    expect(boundary!.summaryText).toBe(summaryPayload);
    expect(transcript.appended.length).toBe(2);
    expect(transcript.appended[0]?.kind).toBe("canonical_message");
    expect(transcript.appended[1]).toBe(boundary);
  });

  it("throws CompactionImpossibleError (§11.6) when the routed model's window is below the min-viable budget", async () => {
    const { client, calls } = mockLLM(() => [
      { kind: "text_delta", blockIndex: 0, delta: "won't-get-here" },
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 0, outputTokens: 0 } },
    ]);
    const engine = new ContextEngine(client, {
      // Hypothetical 2k-window edge: after capping reserve to 20% × 2k
      // = 400, headroom = 2_000 − 400 = 1_600 < minViable 5_000 →
      // compaction is impossible, so maybeCompact must throw BEFORE
      // calling Haiku (and the error must carry the diagnostic fields).
      reserveTokens: 40_000,
      minViableBudgetTokens: 5_000,
      contextWindowResolver: () => 2_000,
    });
    const { session, transcript } = fakeSession();

    const entries: TranscriptEntry[] = [userEntry("t1", "x".repeat(10_000), 1_000)];

    await expect(
      engine.maybeCompact(session, entries, /*tokenLimit*/ 100, "tiny-2k"),
    ).rejects.toBeInstanceOf(CompactionImpossibleError);

    // Re-throw to inspect fields; maybeCompact is deterministic so a
    // second call carries the same payload.
    let captured: CompactionImpossibleError | null = null;
    try {
      await engine.maybeCompact(session, entries, /*tokenLimit*/ 100, "tiny-2k");
    } catch (err) {
      captured = err as CompactionImpossibleError;
    }
    expect(captured).not.toBeNull();
    expect(captured!.model).toBe("tiny-2k");
    expect(captured!.contextWindow).toBe(2_000);
    expect(captured!.effectiveReserveTokens).toBe(400);
    expect(captured!.effectiveBudgetTokens).toBe(1_600);
    expect(captured!.minViableBudgetTokens).toBe(5_000);

    // No boundary written, no Haiku call (pre-flight rejected).
    expect(transcript.appended.length).toBe(0);
    expect(calls.length).toBe(0);
  });

  it("assertCompactionFeasible exposes the §11.6 gate for route-time checks", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client, {
      reserveTokens: 40_000,
      minViableBudgetTokens: 5_000,
      contextWindowResolver: (m) => (m === "tiny-2k" ? 2_000 : 200_000),
    });

    // Large window → feasible (no throw).
    expect(() => engine.assertCompactionFeasible("big-200k")).not.toThrow();

    // Tiny window → throws the tagged error so Session.runTurn can
    // emit the compaction_impossible SSE event before the turn even
    // starts executing tools.
    expect(() => engine.assertCompactionFeasible("tiny-2k")).toThrow(
      CompactionImpossibleError,
    );
  });

  it("model-less maybeCompact preserves legacy behaviour (29d8da97 boundary semantics untouched)", async () => {
    // Existing callers that pass no model id must keep their current
    // behaviour: no §11.6 check, boundaries still written on overflow.
    const summaryPayload = "legacy summary";
    const { client } = mockLLM(() => [
      { kind: "text_delta", blockIndex: 0, delta: summaryPayload },
      { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 0, outputTokens: 0 } },
    ]);
    const engine = new ContextEngine(client, {
      // Even with a resolver that would otherwise declare 2k
      // impossible, omitting `model` must skip the check entirely.
      minViableBudgetTokens: 5_000,
      contextWindowResolver: () => 2_000,
    });
    const { session, transcript } = fakeSession();

    const bigText = "q".repeat(20_000);
    const entries: TranscriptEntry[] = [userEntry("t1", bigText, 1_000)];

    const boundary = await engine.maybeCompact(
      session,
      entries,
      /*tokenLimit*/ 1_000,
      // no model arg
    );
    expect(boundary).not.toBeNull();
    expect(transcript.appended.length).toBe(2);
    expect(transcript.appended[0]?.kind).toBe("canonical_message");
    expect(transcript.appended[1]).toBe(boundary);
  });
});

// ── Bug fix: timestamps in LLM messages ─────────────────────────────

describe("ContextEngine.buildMessagesFromTranscript — timestamps", () => {
  it("includes [Time: ISO] prefix on user_message entries", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const ts = new Date("2026-04-22T10:11:00Z").getTime();
    const entries: TranscriptEntry[] = [
      userEntry("t1", "hello", ts),
      assistantEntry("t1", "world", ts + 1_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    expect(messages[0]?.role).toBe("user");
    const content = messages[0]?.content as string;
    expect(content).toContain("[Time: 2026-04-22T10:11:00.000Z]");
    expect(content).toContain("hello");
  });

  it("does NOT add timestamp prefix to assistant_text entries", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      userEntry("t1", "hi", 1_000),
      assistantEntry("t1", "reply", 2_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    const assistantContent = messages[1]?.content;
    // Assistant content is LLMContentBlock[] — text should not contain [Time:]
    expect(Array.isArray(assistantContent)).toBe(true);
    const textBlock = (assistantContent as Array<{ type: string; text: string }>)[0];
    expect(textBlock?.text).toBe("reply");
    expect(textBlock?.text).not.toContain("[Time:");
  });

  it("preserves timestamps through compaction boundary (post-boundary entries)", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const ts = new Date("2026-04-22T14:00:00Z").getTime();
    const entries: TranscriptEntry[] = [
      userEntry("t1", "old-msg", 1_000),
      assistantEntry("t1", "old-reply", 2_000),
      boundaryEntry("01HB1", "SUMMARY", 3_000),
      userEntry("t2", "new-msg", ts),
      assistantEntry("t2", "new-reply", ts + 1_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    // [0] = boundary summary, [1] = new-msg with timestamp, [2] = new-reply
    expect(messages[1]?.role).toBe("user");
    const content = messages[1]?.content as string;
    expect(content).toContain("[Time: 2026-04-22T14:00:00.000Z]");
    expect(content).toContain("new-msg");
  });
});

// ── Bug fix: tool_call/tool_result in message reconstruction ──────────

describe("ContextEngine.buildMessagesFromTranscript — tool blocks", () => {
  it("includes tool_use in assistant message and tool_result in user message", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      userEntry("t1", "read file.txt", 1_000),
      assistantEntry("t1", "I'll read that file.", 2_000),
      toolCallEntry("t1", "tu_1", "FileRead", { path: "/file.txt" }, 3_000),
      toolResultEntry("t1", "tu_1", "file contents here", 4_000),
      assistantEntry("t1", "The file contains...", 5_000),
      committedEntry("t1", 6_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    // Expected sequence:
    // [0] user: "read file.txt"
    // [1] assistant: [text "I'll read that file.", tool_use FileRead]
    // [2] user: [tool_result]
    // [3] assistant: [text "The file contains..."]
    expect(messages.length).toBe(4);
    expect(messages[0]?.role).toBe("user");

    // Assistant message should have text + tool_use merged
    expect(messages[1]?.role).toBe("assistant");
    const assistantBlocks = messages[1]?.content as Array<{ type: string }>;
    expect(Array.isArray(assistantBlocks)).toBe(true);
    expect(assistantBlocks.some((b) => b.type === "text")).toBe(true);
    expect(assistantBlocks.some((b) => b.type === "tool_use")).toBe(true);

    // Tool result should be a user message
    expect(messages[2]?.role).toBe("user");
    const resultBlocks = messages[2]?.content as Array<{ type: string }>;
    expect(Array.isArray(resultBlocks)).toBe(true);
    expect(resultBlocks[0]?.type).toBe("tool_result");

    // Final assistant text
    expect(messages[3]?.role).toBe("assistant");
  });

  it("batches multiple tool_results into a single user message", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      userEntry("t1", "search for bugs", 1_000),
      toolCallEntry("t1", "tu_1", "Grep", { pattern: "bug" }, 2_000),
      toolCallEntry("t1", "tu_2", "Glob", { pattern: "*.ts" }, 2_001),
      toolResultEntry("t1", "tu_1", "found bug at line 42", 3_000),
      toolResultEntry("t1", "tu_2", "file1.ts\nfile2.ts", 3_001),
      assistantEntry("t1", "Found the bug.", 4_000),
      committedEntry("t1", 5_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    // [0] user: "search for bugs"
    // [1] assistant: [tool_use Grep, tool_use Glob]
    // [2] user: [tool_result tu_1, tool_result tu_2]
    // [3] assistant: "Found the bug."
    expect(messages.length).toBe(4);
    expect(messages[1]?.role).toBe("assistant");
    const assistantBlocks = messages[1]?.content as Array<{ type: string }>;
    expect(assistantBlocks.filter((b) => b.type === "tool_use").length).toBe(2);

    expect(messages[2]?.role).toBe("user");
    const resultBlocks = messages[2]?.content as Array<{ type: string }>;
    expect(resultBlocks.filter((b) => b.type === "tool_result").length).toBe(2);
  });

  it("orders replayed tool_results to match the assistant tool_use order", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      userEntry("t1", "collect both facts", 1_000),
      toolCallEntry("t1", "tu_bash", "Bash", { command: "kb-search" }, 2_000),
      toolCallEntry("t1", "tu_artifact", "ArtifactList", { kind: "report" }, 2_001),
      // Parallel tools persist transcript results in completion order,
      // which can differ from the assistant's tool_use order.
      toolResultEntry("t1", "tu_artifact", "artifact list", 3_000),
      toolResultEntry("t1", "tu_bash", "search output", 3_001),
      { kind: "turn_aborted", ts: 4_000, turnId: "t1", reason: "upstream 400" },
      userEntry("t2", "continue", 5_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    const assistant = messages.find((message) => message.role === "assistant");
    const user = messages[messages.findIndex((message) => message === assistant) + 1];
    const assistantBlocks = assistant?.content as Array<{ type: string; id?: string }>;
    const resultBlocks = user?.content as Array<{ type: string; tool_use_id?: string }>;

    expect(assistantBlocks.filter((block) => block.type === "tool_use").map((block) => block.id))
      .toEqual(["tu_bash", "tu_artifact"]);
    expect(resultBlocks.filter((block) => block.type === "tool_result").map((block) => block.tool_use_id))
      .toEqual(["tu_bash", "tu_artifact"]);
  });

  it("maintains strict user/assistant alternation across multiple turns", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      // Turn 1 — simple text exchange
      userEntry("t1", "hello", 1_000),
      assistantEntry("t1", "hi there", 2_000),
      committedEntry("t1", 3_000),
      // Turn 2 — tool use
      userEntry("t2", "read file", 4_000),
      assistantEntry("t2", "reading...", 5_000),
      toolCallEntry("t2", "tu_1", "FileRead", {}, 6_000),
      toolResultEntry("t2", "tu_1", "contents", 7_000),
      assistantEntry("t2", "done reading", 8_000),
      committedEntry("t2", 9_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    // Verify strict alternation: no two consecutive same-role messages
    for (let i = 1; i < messages.length; i++) {
      expect(messages[i]?.role).not.toBe(messages[i - 1]?.role);
    }
  });

  it("handles tool-only turn (no assistant text before tool_use)", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      userEntry("t1", "check status", 1_000),
      toolCallEntry("t1", "tu_1", "Bash", { command: "ls" }, 2_000),
      toolResultEntry("t1", "tu_1", "file1 file2", 3_000),
      assistantEntry("t1", "Here are the files.", 4_000),
      committedEntry("t1", 5_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    // [0] user: "check status"
    // [1] assistant: [tool_use Bash]
    // [2] user: [tool_result]
    // [3] assistant: "Here are the files."
    expect(messages.length).toBe(4);
    expect(messages[0]?.role).toBe("user");
    expect(messages[1]?.role).toBe("assistant");
    expect(messages[2]?.role).toBe("user");
    expect(messages[3]?.role).toBe("assistant");
  });

  it("merges consecutive user messages from aborted turns (no assistant_text)", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    // Aborted turn: user_message written but NO assistant_text (only on commit)
    // Next turn: user_message added
    // Without merge: user → user (model answers first, ignores second)
    const entries: TranscriptEntry[] = [
      userEntry("t1", "이전 질문", 1_000),
      // turn_aborted — no assistant_text written
      { kind: "turn_aborted", ts: 2_000, turnId: "t1", reason: "beforeCommit blocked" },
      userEntry("t2", "현재 질문", 3_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    // Should merge into a single user message, not two consecutive ones
    expect(messages.length).toBe(1);
    expect(messages[0]?.role).toBe("user");
    // Both texts should be present
    const content = messages[0]?.content;
    expect(Array.isArray(content)).toBe(true);
    const texts = (content as Array<{ type: string; text: string }>)
      .filter((b) => b.type === "text")
      .map((b) => b.text);
    expect(texts.some((t) => t.includes("이전 질문"))).toBe(true);
    expect(texts.some((t) => t.includes("현재 질문"))).toBe(true);
  });

  it("merges consecutive user messages from aborted tool turns (tool_result + user)", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    // Aborted turn with tools: user + tool_call + tool_result + turn_aborted
    // Then new turn: user_message
    const entries: TranscriptEntry[] = [
      userEntry("t1", "analyze file", 1_000),
      toolCallEntry("t1", "tu_1", "FileRead", { path: "/x" }, 2_000),
      toolResultEntry("t1", "tu_1", "file content", 3_000),
      { kind: "turn_aborted", ts: 4_000, turnId: "t1", reason: "blocked" },
      userEntry("t2", "다른 질문", 5_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    // Verify no consecutive same-role messages
    for (let i = 1; i < messages.length; i++) {
      expect(messages[i]?.role).not.toBe(messages[i - 1]?.role);
    }
    // Last message should contain "다른 질문"
    const last = messages[messages.length - 1]!;
    expect(last.role).toBe("user");
    const lastContent = Array.isArray(last.content)
      ? last.content.map((b) => (b as { text?: string }).text ?? "").join("")
      : last.content;
    expect(lastContent).toContain("다른 질문");
  });

  it("strips orphaned tool_use when tool_result is missing (aborted mid-execution)", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    // Turn aborted after tool_call written but BEFORE tool_result
    const entries: TranscriptEntry[] = [
      userEntry("t1", "read the file", 1_000),
      assistantEntry("t1", "reading...", 2_000),
      toolCallEntry("t1", "tu_orphan", "FileRead", { path: "/x" }, 3_000),
      // NO tool_result — turn was aborted mid-execution
      { kind: "turn_aborted", ts: 4_000, turnId: "t1", reason: "timeout" },
      userEntry("t2", "next question", 5_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    // Orphaned tool_use should be STRIPPED (Anthropic API rejects it)
    for (const msg of messages) {
      if (!Array.isArray(msg.content)) continue;
      for (const block of msg.content) {
        expect(block.type).not.toBe("tool_use");
      }
    }
    // Should still have the user messages
    const lastMsg = messages[messages.length - 1]!;
    const content = Array.isArray(lastMsg.content)
      ? lastMsg.content.map((b) => (b as { text?: string }).text ?? "").join("")
      : lastMsg.content;
    expect(content).toContain("next question");
  });

  it("keeps valid tool_use/tool_result pairs intact", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      userEntry("t1", "search", 1_000),
      toolCallEntry("t1", "tu_1", "Grep", { pattern: "x" }, 2_000),
      toolResultEntry("t1", "tu_1", "found it", 3_000),
      assistantEntry("t1", "done", 4_000),
      committedEntry("t1", 5_000),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    // tool_use and tool_result should both be present
    let hasToolUse = false;
    let hasToolResult = false;
    for (const msg of messages) {
      if (!Array.isArray(msg.content)) continue;
      for (const block of msg.content) {
        if (block.type === "tool_use") hasToolUse = true;
        if (block.type === "tool_result") hasToolResult = true;
      }
    }
    expect(hasToolUse).toBe(true);
    expect(hasToolResult).toBe(true);
  });

  it("keeps tool_use when matching tool_result was merged after user text (position-independent)", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      canonicalAssistantEntry("t1", [
        { type: "text", text: "Checking task output." },
        {
          type: "tool_use",
          id: "functions.TaskOutput:0",
          name: "TaskOutput",
          input: { taskId: "spawn_1" },
        },
      ]),
      // A later interrupted turn can merge the user's text before a
      // same-id tool_result. After mergeConsecutiveSameRole, the user
      // message contains [text, tool_result]. The position-independent
      // scanner must still match the tool_result and keep both blocks.
      userEntry("t2", "어캐됐어", 4_000),
      toolResultEntry("t2", "functions.TaskOutput:0", "late result", 4_001),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    const assistant = messages.find((msg) => msg.role === "assistant");
    expect(assistant).toBeDefined();
    expect(Array.isArray(assistant?.content)).toBe(true);
    // tool_use should be kept because the tool_result matches
    expect((assistant?.content as Array<{ type: string }>).some(
      (block) => block.type === "tool_use",
    )).toBe(true);

    const last = messages[messages.length - 1]!;
    expect(last.role).toBe("user");
    expect(Array.isArray(last.content)).toBe(true);
    const userBlocks = last.content as Array<{ type: string; text?: string }>;
    // tool_result should be reordered before text
    expect(userBlocks[0]?.type).toBe("tool_result");
    expect(userBlocks.some((b) => b.type === "text" && b.text?.includes("어캐됐어"))).toBe(true);
  });

  it("deduplicates repeated tool_results for the same tool_use id", () => {
    const { client } = mockLLM(() => []);
    const engine = new ContextEngine(client);

    const entries: TranscriptEntry[] = [
      canonicalAssistantEntry("t1", [
        { type: "text", text: "Running shell." },
        {
          type: "tool_use",
          id: "functions_Bash_1",
          name: "Bash",
          input: { command: "pwd" },
        },
      ]),
      toolResultEntry("t1", "functions_Bash_1", "/workspace", 4_000),
      toolResultEntry("t1", "functions_Bash_1", "/workspace duplicate", 4_001),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    const user = messages.find((msg) => msg.role === "user");
    expect(user).toBeDefined();
    expect(Array.isArray(user?.content)).toBe(true);
    const resultBlocks = (user?.content as Array<{ type: string; tool_use_id?: string }>)
      .filter((block) => block.type === "tool_result");
    expect(resultBlocks).toHaveLength(1);
    expect(resultBlocks[0]?.tool_use_id).toBe("functions_Bash_1");
  });
});
