import { describe, expect, it } from "vitest";
import { clipMessagesAtResetBoundary } from "./clip-messages-at-reset-boundary";
import type { ChatMessage } from "./types";

function msg(id: string, content: string, timestamp: number, role: ChatMessage["role"] = "user"): ChatMessage {
  return { id, role, content, timestamp };
}

describe("clipMessagesAtResetBoundary", () => {
  it("returns history unchanged when no reset has happened", () => {
    const history = [msg("a", "hi", 100), msg("b", "answer", 101, "assistant")];
    expect(clipMessagesAtResetBoundary(history, null)).toEqual(history);
  });

  it("drops every message older than the reset boundary", () => {
    // Simulates the dashboard symptom: a multi-turn 1+1 cross-validation
    // exchange is in `messages`, then the user clicks Reset (boundary=500),
    // then sends a casual greeting. Without clipping, the bot would resume the
    // old task — exactly the reported bug.
    const history = [
      msg("u1", "서로다른 세 SOTA 모델로...", 100),
      msg("a1", "분석 진행하겠습니다", 200, "assistant"),
      msg("u2", "계속해", 300),
      msg("a2", "도구 호출 중...", 400, "assistant"),
      msg("divider", "Session ended — new conversation started", 500, "system"),
      msg("u3", "ㅎㅇ", 600),
    ];
    const clipped = clipMessagesAtResetBoundary(history, 500);
    expect(clipped.map((m) => m.id)).toEqual(["divider", "u3"]);
  });

  it("keeps a message that lands exactly at the boundary timestamp", () => {
    const at = 1000;
    const history = [msg("old", "x", 999), msg("boundary", "y", at)];
    expect(clipMessagesAtResetBoundary(history, at).map((m) => m.id)).toEqual(["boundary"]);
  });

  it("treats a missing/zero timestamp as before any positive boundary", () => {
    const history = [msg("no-ts", "x", 0), msg("post", "y", 100)];
    expect(clipMessagesAtResetBoundary(history, 50).map((m) => m.id)).toEqual(["post"]);
  });

  it("is null/undefined safe", () => {
    expect(clipMessagesAtResetBoundary(null, 100)).toEqual([]);
    expect(clipMessagesAtResetBoundary(undefined, 100)).toEqual([]);
    expect(clipMessagesAtResetBoundary([], 100)).toEqual([]);
  });

  it("ignores a non-finite boundary (defensive)", () => {
    const history = [msg("a", "x", 100)];
    expect(clipMessagesAtResetBoundary(history, Number.NaN)).toEqual(history);
    expect(clipMessagesAtResetBoundary(history, Number.POSITIVE_INFINITY)).toEqual(history);
  });
});
