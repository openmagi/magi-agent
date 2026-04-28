/**
 * Transcript.readCommitted — bug fix tests.
 *
 * Bug: readCommitted() returns [] when all turns are aborted (no
 * turn_committed entries). This causes bot amnesia — the LLM sees
 * no prior conversation even though user_message + assistant_text
 * entries exist in the JSONL file.
 *
 * Fix: treat turn_aborted as a valid boundary (same as turn_committed)
 * so aborted turns are included in the conversation history.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Transcript, type TranscriptEntry } from "./Transcript.js";

let tmpDir: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "transcript-test-"));
  await fs.mkdir(path.join(tmpDir, "sessions"), { recursive: true });
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

function makeTranscript(sessionKey = "agent:main:test:1"): Transcript {
  return new Transcript(path.join(tmpDir, "sessions"), sessionKey);
}

async function writeEntries(
  transcript: Transcript,
  entries: TranscriptEntry[],
): Promise<void> {
  const content = entries.map((e) => JSON.stringify(e)).join("\n") + "\n";
  await fs.mkdir(path.dirname(transcript.filePath), { recursive: true });
  await fs.writeFile(transcript.filePath, content);
}

describe("Transcript.readCommitted", () => {
  it("returns entries up to last turn_committed (existing behavior)", async () => {
    const t = makeTranscript();
    await writeEntries(t, [
      { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
      { kind: "user_message", ts: 2, turnId: "t1", text: "hello" },
      { kind: "assistant_text", ts: 3, turnId: "t1", text: "world" },
      { kind: "turn_committed", ts: 4, turnId: "t1", inputTokens: 100, outputTokens: 50 },
    ]);
    const committed = await t.readCommitted();
    expect(committed.length).toBe(4);
    expect(committed[1]!.kind).toBe("user_message");
  });

  it("returns [] when transcript is empty", async () => {
    const t = makeTranscript();
    const committed = await t.readCommitted();
    expect(committed).toEqual([]);
  });

  it("includes entries up to turn_aborted when no turn_committed exists", async () => {
    const t = makeTranscript();
    await writeEntries(t, [
      { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
      { kind: "user_message", ts: 2, turnId: "t1", text: "analyze this" },
      { kind: "assistant_text", ts: 3, turnId: "t1", text: "Here is my analysis..." },
      { kind: "turn_aborted", ts: 4, turnId: "t1", reason: "beforeCommit blocked" },
    ]);
    const committed = await t.readCommitted();
    // BUG: this returned [] before the fix — bot lost all history
    expect(committed.length).toBe(4);
    expect(committed[1]!.kind).toBe("user_message");
    expect((committed[1] as { text: string }).text).toBe("analyze this");
    expect(committed[2]!.kind).toBe("assistant_text");
  });

  it("includes aborted turns when mixed with committed turns", async () => {
    const t = makeTranscript();
    await writeEntries(t, [
      // Turn 1 — committed
      { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
      { kind: "user_message", ts: 2, turnId: "t1", text: "first" },
      { kind: "assistant_text", ts: 3, turnId: "t1", text: "reply1" },
      { kind: "turn_committed", ts: 4, turnId: "t1", inputTokens: 100, outputTokens: 50 },
      // Turn 2 — aborted (factGroundingVerifier blocked commit)
      { kind: "turn_started", ts: 5, turnId: "t2", declaredRoute: "direct" },
      { kind: "user_message", ts: 6, turnId: "t2", text: "second" },
      { kind: "assistant_text", ts: 7, turnId: "t2", text: "reply2" },
      { kind: "turn_aborted", ts: 8, turnId: "t2", reason: "beforeCommit blocked" },
    ]);
    const committed = await t.readCommitted();
    // Should include BOTH turns — committed + aborted
    expect(committed.length).toBe(8);
    expect(committed[5]!.kind).toBe("user_message");
    expect((committed[5] as { text: string }).text).toBe("second");
  });

  it("stops at the last completed turn boundary, not trailing uncommitted entries", async () => {
    const t = makeTranscript();
    await writeEntries(t, [
      { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
      { kind: "user_message", ts: 2, turnId: "t1", text: "first" },
      { kind: "assistant_text", ts: 3, turnId: "t1", text: "reply" },
      { kind: "turn_committed", ts: 4, turnId: "t1", inputTokens: 100, outputTokens: 50 },
      // Turn 2 — in-progress (no committed or aborted yet)
      { kind: "turn_started", ts: 5, turnId: "t2", declaredRoute: "direct" },
      { kind: "user_message", ts: 6, turnId: "t2", text: "in-progress" },
    ]);
    const committed = await t.readCommitted();
    // Should only include turn 1 (the trailing turn 2 entries are not yet complete)
    expect(committed.length).toBe(4);
  });

  it("serves repeated readAll calls from cache when file stats are unchanged", async () => {
    const t = makeTranscript();
    await writeEntries(t, [
      { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
      { kind: "user_message", ts: 2, turnId: "t1", text: "first" },
      { kind: "turn_committed", ts: 3, turnId: "t1", inputTokens: 1, outputTokens: 1 },
    ]);
    const readSpy = vi.spyOn(fs, "readFile");

    const first = await t.readAll();
    const second = await t.readAll();

    expect(first).toEqual(second);
    expect(readSpy).toHaveBeenCalledTimes(1);
    readSpy.mockRestore();
  });

  it("invalidates the readAll cache when the transcript file changes externally", async () => {
    const t = makeTranscript();
    await writeEntries(t, [
      { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
      { kind: "turn_committed", ts: 2, turnId: "t1", inputTokens: 1, outputTokens: 1 },
    ]);
    const readSpy = vi.spyOn(fs, "readFile");

    await t.readAll();
    await fs.appendFile(
      t.filePath,
      `${JSON.stringify({
        kind: "control_event",
        ts: 3,
        seq: 1,
        eventId: "ce_1",
        eventType: "verification",
      } satisfies TranscriptEntry)}\n`,
      "utf8",
    );
    const refreshed = await t.readAll();

    expect(refreshed).toHaveLength(3);
    expect(readSpy).toHaveBeenCalledTimes(2);
    readSpy.mockRestore();
  });
});
