import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { ControlEventLedger } from "./ControlEventLedger.js";
import { projectControlEvents } from "./ControlProjection.js";
import { Transcript } from "../storage/Transcript.js";

async function tmpdir(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), "control-ledger-"));
}

describe("ControlEventLedger", () => {
  it("assigns durable event ids and monotonic sequence numbers", async () => {
    const root = await tmpdir();
    const ledger = new ControlEventLedger({ rootDir: root, sessionKey: "session-a" });

    const first = await ledger.append({
      type: "retry",
      turnId: "t1",
      reason: "before_commit_blocked",
      attempt: 1,
      maxAttempts: 3,
      visibleToUser: true,
    });
    const second = await ledger.append({
      type: "stop_reason",
      turnId: "t1",
      reason: "end_turn",
    });

    expect(first.seq).toBe(1);
    expect(second.seq).toBe(2);
    expect(first.eventId).toMatch(/^ce_/);
    expect(await ledger.readSince(1)).toHaveLength(1);
    expect(await ledger.readByTurn("t1")).toHaveLength(2);
  });

  it("rejects unknown event types before appending", async () => {
    const root = await tmpdir();
    const ledger = new ControlEventLedger({ rootDir: root, sessionKey: "session-a" });

    await expect(
      ledger.append({
        type: "not_real",
        turnId: "t1",
      } as never),
    ).rejects.toThrow(/unknown control event type/);
    expect(await ledger.readAll()).toEqual([]);
  });

  it("ignores a malformed tail while preserving earlier events", async () => {
    const root = await tmpdir();
    const ledger = new ControlEventLedger({ rootDir: root, sessionKey: "session-a" });
    await ledger.append({ type: "stop_reason", turnId: "t1", reason: "end_turn" });
    await fs.appendFile(ledger.filePath, "{\"type\":\"broken\"\n", "utf8");

    const events = await ledger.readAll();
    expect(events).toHaveLength(1);
    expect(projectControlEvents(events)).toMatchObject({
      lastSeq: 1,
      lastStopReasonByTurn: { t1: "end_turn" },
    });
  });

  it("serializes concurrent append sequence numbers across ledger instances", async () => {
    const root = await tmpdir();
    const a = new ControlEventLedger({ rootDir: root, sessionKey: "session-a" });
    const b = new ControlEventLedger({ rootDir: root, sessionKey: "session-a" });

    const appended = await Promise.all(
      Array.from({ length: 20 }, (_, i) =>
        (i % 2 === 0 ? a : b).append({
          type: "stop_reason",
          turnId: `t${i}`,
          reason: "end_turn",
        }),
      ),
    );

    expect(new Set(appended.map((event) => event.seq)).size).toBe(20);
    expect(appended.map((event) => event.seq).sort((x, y) => x - y)).toEqual(
      Array.from({ length: 20 }, (_, i) => i + 1),
    );
  });

  it("keeps later valid events visible after a crash-partial JSONL tail", async () => {
    const root = await tmpdir();
    const ledger = new ControlEventLedger({ rootDir: root, sessionKey: "session-a" });
    await ledger.append({ type: "stop_reason", turnId: "t1", reason: "end_turn" });
    await fs.appendFile(ledger.filePath, "{\"type\":\"partial\"", "utf8");
    await ledger.append({ type: "stop_reason", turnId: "t2", reason: "end_turn" });

    expect((await ledger.readAll()).map((event) => event.turnId)).toEqual([
      "t1",
      "t2",
    ]);
  });

  it("mirrors non-turn control events into the transcript", async () => {
    const root = await tmpdir();
    const transcript = new Transcript(root, "session-a");
    const ledger = new ControlEventLedger({
      rootDir: root,
      sessionKey: "session-a",
      transcript,
    });
    const event = await ledger.append({
      type: "control_request_created",
      request: {
        requestId: "cr_test",
        kind: "tool_permission",
        state: "pending",
        sessionKey: "session-a",
        source: "turn",
        prompt: "Run Bash?",
        createdAt: Date.now(),
        expiresAt: Date.now() + 60_000,
      },
    });

    expect(await transcript.readAll()).toContainEqual({
      kind: "control_event",
      ts: event.ts,
      seq: event.seq,
      eventId: event.eventId,
      eventType: "control_request_created",
    });
  });

  it("validates required fields for every persisted event family", async () => {
    const root = await tmpdir();
    const ledger = new ControlEventLedger({ rootDir: root, sessionKey: "session-a" });

    await expect(
      ledger.append({
        type: "permission_decision",
        decision: "allow",
      } as never),
    ).rejects.toThrow(/source/);
    await expect(
      ledger.append({
        type: "child_tool_request",
        taskId: "child-1",
      } as never),
    ).rejects.toThrow(/requestId/);
  });
});
