import { describe, expect, it } from "vitest";
import { buildVisibleModelContextMessages } from "./model-context";
import type { ChatMessage } from "./types";

function msg(
  id: string,
  role: ChatMessage["role"],
  content: string,
  timestamp: number,
  serverId?: string,
): ChatMessage {
  return { id, role, content, timestamp, ...(serverId ? { serverId } : {}) };
}

describe("buildVisibleModelContextMessages", () => {
  it("includes server-visible assistant history that is rendered but not in local E2EE state", () => {
    const local = [
      msg("user-1800000000000", "user", "Earlier user request", 1_800_000_000_000),
      msg("user-1800000020000", "user", "What happened after that?", 1_800_000_020_000),
    ];
    const server = [
      msg(
        "server-assistant-1",
        "assistant",
        "Server-only assistant answer visible in the channel.",
        1_800_000_010_000,
        "server-assistant-1",
      ),
    ];

    const context = buildVisibleModelContextMessages(local, server);

    expect(context.map((message) => [message.role, message.content])).toEqual([
      ["user", "Earlier user request"],
      ["assistant", "Server-only assistant answer visible in the channel."],
      ["user", "What happened after that?"],
    ]);
  });

  it("dedupes late server copies of optimistic local messages before model submission", () => {
    const context = buildVisibleModelContextMessages(
      [
        msg("user-1800000000000", "user", "Do the thing", 1_800_000_000_000),
        msg("assistant-1800000010000", "assistant", "Done.", 1_800_000_010_000),
      ],
      [
        msg(
          "server-assistant-1",
          "assistant",
          "Done.",
          1_800_000_011_000,
          "server-assistant-1",
        ),
      ],
    );

    expect(context.map((message) => message.content)).toEqual([
      "Do the thing",
      "Done.",
    ]);
  });

  it("bounds long histories while preserving the latest user turn", () => {
    const local = Array.from({ length: 12 }, (_, index) =>
      msg(
        `assistant-${1_800_000_000_000 + index}`,
        index % 2 === 0 ? "user" : "assistant",
        `message ${index}`,
        1_800_000_000_000 + index,
      )
    );

    const context = buildVisibleModelContextMessages(local, [], 4);

    expect(context).toHaveLength(4);
    expect(context.at(-1)?.content).toBe("message 11");
    expect(context.some((message) => message.content === "message 10")).toBe(true);
  });

  function resetDivider(timestamp: number): ChatMessage {
    return {
      id: `system-reset-${timestamp}`,
      role: "system",
      content: "Session ended — new conversation started",
      timestamp,
    };
  }

  it("drops messages from before the most recent session reset divider", () => {
    const local = [
      msg("user-1", "user", "내외디스틸러리 딥리서치 해줘", 1_800_000_000_000),
      msg("assistant-1", "assistant", "분석 결과입니다...", 1_800_000_001_000),
      resetDivider(1_800_000_002_000),
      msg("user-2", "user", "ㅎㅇ", 1_800_000_003_000),
    ];

    const context = buildVisibleModelContextMessages(local, []);

    expect(context.map((message) => message.content)).toEqual(["ㅎㅇ"]);
  });

  it("scopes to the latest session when multiple resets exist", () => {
    const local = [
      msg("user-1", "user", "first session", 1_000),
      resetDivider(2_000),
      msg("user-2", "user", "second session", 3_000),
      resetDivider(4_000),
      msg("user-3", "user", "third session", 5_000),
    ];

    const context = buildVisibleModelContextMessages(local, []);

    expect(context.map((message) => message.content)).toEqual(["third session"]);
  });

  it("drops server-visible messages from before the reset boundary", () => {
    const local = [
      resetDivider(2_000),
      msg("user-2", "user", "after reset", 3_000),
    ];
    const server = [
      msg("server-1", "assistant", "before reset answer", 1_000, "server-1"),
    ];

    const context = buildVisibleModelContextMessages(local, server);

    expect(context.map((message) => message.content)).toEqual(["after reset"]);
  });

  it("drops late server-visible messages from the previous session after reset", () => {
    const local = [
      msg("user-old", "user", "old canary prompt", 1_000),
      resetDivider(2_000),
      msg("user-new", "user", "2 + 2?", 3_000),
    ];
    const server = [
      msg(
        "server-late-old-assistant",
        "assistant",
        "Summary of the old canary/system prompt.",
        4_000,
        "server-late-old-assistant",
      ),
    ];

    const context = buildVisibleModelContextMessages(local, server);

    expect(context.map((message) => [message.role, message.content])).toEqual([
      ["user", "2 + 2?"],
    ]);
  });

  it("uses a persisted reset boundary when the local divider is not loaded", () => {
    const local = [
      msg("user-new", "user", "내 직전 질문이 뭐였지?", 5_000),
    ];
    const server = [
      msg("server-old-user", "user", "SAFE CANARY ONLY old prompt", 1_000, "server-old-user"),
      msg("server-old-assistant", "assistant", "old answer", 2_000, "server-old-assistant"),
      msg("server-new-copy", "user", "내 직전 질문이 뭐였지?", 5_000, "server-new-copy"),
    ];

    const context = buildVisibleModelContextMessages(local, server, 32, 4_000);

    expect(context.map((message) => [message.role, message.content])).toEqual([
      ["user", "내 직전 질문이 뭐였지?"],
    ]);
  });

  it("drops operational canary prompts and their assistant receipts", () => {
    const local = [
      msg("canary-user", "user", "SAFE CANARY ONLY. Confirm route health.", 1_000),
      msg("canary-assistant", "assistant", "Canary receipt: route OK.", 2_000),
      msg("real-user", "user", "What did I actually ask?", 3_000),
    ];

    const context = buildVisibleModelContextMessages(local, []);

    expect(context.map((message) => [message.role, message.content])).toEqual([
      ["user", "What did I actually ask?"],
    ]);
  });

  it("drops every assistant diagnostic reply until the next real user turn", () => {
    const local = [
      msg("diagnostic-user", "user", "internal diagnostic smoke: confirm hosted chat context", 1_000),
      msg("diagnostic-assistant-1", "assistant", "Diagnostic route OK.", 2_000),
      msg("diagnostic-assistant-2", "assistant", "Additional hidden diagnostic detail.", 3_000),
      msg("real-user", "user", "Use only this message.", 4_000),
      msg("real-assistant", "assistant", "Visible answer.", 5_000),
    ];

    const context = buildVisibleModelContextMessages(local, []);

    expect(context.map((message) => [message.role, message.content])).toEqual([
      ["user", "Use only this message."],
      ["assistant", "Visible answer."],
    ]);
  });

  it("does not treat non-reset system messages as a session boundary", () => {
    const local = [
      msg("user-1", "user", "keep me", 1_000),
      msg("server-system-1", "system", "server notice", 2_000),
      msg("user-2", "user", "and me", 3_000),
    ];

    const context = buildVisibleModelContextMessages(local, []);

    expect(context.map((message) => message.content)).toEqual(["keep me", "and me"]);
  });
});
