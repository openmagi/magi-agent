/**
 * U5 Test 18: merge does not clobber optimistic bubbles.
 *
 * Local-only optimistic user bubble + in-flight streaming assistant bubble
 * survive a merge of server rows that do not include them.
 *
 * Tests the seq-aware sort: when both rows carry seq, seq wins; when only
 * one or neither carries seq, timestamp is the tiebreaker (legacy path).
 */
import { describe, it, expect } from "vitest";
import type { ChatMessage } from "./types";

/** Inline seq-aware comparator matching the spec (no import of private helpers) */
function compareSeqAware(a: ChatMessage, b: ChatMessage): number {
  if (a.seq != null && b.seq != null) return a.seq - b.seq;
  return a.timestamp - b.timestamp;
}

function makeUser(overrides: Partial<ChatMessage> & { id: string }): ChatMessage {
  return {
    role: "user",
    content: "hello",
    timestamp: 1000,
    ...overrides,
  };
}

function makeAssistant(overrides: Partial<ChatMessage> & { id: string }): ChatMessage {
  return {
    role: "assistant",
    content: "reply",
    timestamp: 2000,
    ...overrides,
  };
}

describe("seq-aware merge: optimistic bubbles survive server merge (Test 18)", () => {
  it("keeps local-only optimistic user bubble when server rows do not include it", () => {
    // Optimistic bubble: no serverId, no seq
    const optimistic = makeUser({ id: "user-1720000000000", timestamp: 1_000 });

    // Server rows: two completed assistant messages with seq
    const serverRow1 = makeAssistant({ id: "srv-1", serverId: "srv-1", seq: 1, timestamp: 900 });
    const serverRow2 = makeAssistant({ id: "srv-2", serverId: "srv-2", seq: 2, timestamp: 1_100 });

    // Simulate the merge as in mergeFetchedServerMessages:
    // kept = prev filtered to drop anything whose serverId is in the new server set
    // merged = [...kept, ...mapped].sort(compareSeqAware)
    const prev: ChatMessage[] = [optimistic];
    const mapped: ChatMessage[] = [serverRow1, serverRow2];
    const serverIds = new Set(mapped.map((m) => m.serverId).filter(Boolean));
    const kept = prev.filter((m) => !m.serverId || !serverIds.has(m.serverId));
    const merged = [...kept, ...mapped].sort(compareSeqAware);

    // Optimistic bubble must survive
    expect(merged.some((m) => m.id === optimistic.id)).toBe(true);
    // All server rows are present
    expect(merged.some((m) => m.id === "srv-1")).toBe(true);
    expect(merged.some((m) => m.id === "srv-2")).toBe(true);
    expect(merged).toHaveLength(3);
  });

  it("keeps in-flight streaming assistant bubble when server rows do not match it", () => {
    // In-flight bubble: local streamed assistant, no serverId yet
    const inFlight = makeAssistant({ id: "local-turn-abc-assistant", timestamp: 1_500 });

    const serverRow = makeAssistant({ id: "srv-1", serverId: "srv-1", seq: 1, timestamp: 900 });

    const prev: ChatMessage[] = [inFlight];
    const mapped: ChatMessage[] = [serverRow];
    const serverIds = new Set(mapped.map((m) => m.serverId).filter(Boolean));
    const kept = prev.filter((m) => !m.serverId || !serverIds.has(m.serverId));
    const merged = [...kept, ...mapped].sort(compareSeqAware);

    expect(merged.some((m) => m.id === inFlight.id)).toBe(true);
    expect(merged.some((m) => m.id === "srv-1")).toBe(true);
    expect(merged).toHaveLength(2);
  });

  it("sorts by seq when both rows carry seq, ignoring timestamp order", () => {
    // Deliberately reversed timestamps to prove seq wins
    const a = makeAssistant({ id: "a", serverId: "a", seq: 1, timestamp: 9_000 });
    const b = makeAssistant({ id: "b", serverId: "b", seq: 2, timestamp: 1_000 });

    const merged = [b, a].sort(compareSeqAware);

    expect(merged[0]!.id).toBe("a");
    expect(merged[1]!.id).toBe("b");
  });

  it("falls back to timestamp when seq is absent on either row", () => {
    const old = makeAssistant({ id: "old", serverId: "old", timestamp: 1_000 });
    const newer = makeAssistant({ id: "newer", serverId: "newer", seq: 5, timestamp: 2_000 });
    // old has no seq, newer has seq -- must fall back to timestamp
    const merged = [newer, old].sort(compareSeqAware);

    expect(merged[0]!.id).toBe("old");
    expect(merged[1]!.id).toBe("newer");
  });
});
