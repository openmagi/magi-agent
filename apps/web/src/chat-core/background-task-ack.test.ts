import { describe, expect, it } from "vitest";
import {
  isBackgroundTaskAck,
  buildBackgroundTaskAckCard,
  parseBackgroundTaskAckFromMessage,
} from "./background-task-ack";

describe("isBackgroundTaskAck", () => {
  it("matches a RunInBackground tool result by toolName", () => {
    expect(isBackgroundTaskAck({ toolName: "RunInBackground", output: {} })).toBe(true);
  });

  it("rejects other tools", () => {
    expect(isBackgroundTaskAck({ toolName: "WebSearch", output: {} })).toBe(false);
    expect(isBackgroundTaskAck({ toolName: "TaskList", output: {} })).toBe(false);
  });

  it("rejects non-object / missing toolName", () => {
    expect(isBackgroundTaskAck(null)).toBe(false);
    expect(isBackgroundTaskAck(undefined)).toBe(false);
    expect(isBackgroundTaskAck({})).toBe(false);
    expect(isBackgroundTaskAck("RunInBackground")).toBe(false);
  });
});

describe("buildBackgroundTaskAckCard", () => {
  it("extracts task id, short id, title, status, ack, and board href", () => {
    const card = buildBackgroundTaskAckCard({
      toolName: "RunInBackground",
      output: {
        taskId: "abcdef123456",
        status: "todo",
        title: "Write Q2 report",
        goalMode: false,
        ack: "Started in background (task abcdef)",
      },
    });
    expect(card).not.toBeNull();
    expect(card?.taskId).toBe("abcdef123456");
    expect(card?.shortId).toBe("abcdef");
    expect(card?.title).toBe("Write Q2 report");
    expect(card?.status).toBe("todo");
    expect(card?.goalMode).toBe(false);
    expect(card?.ack).toContain("Started in background");
    expect(card?.boardHref).toBe("/dashboard/work-queue");
  });

  it("returns null when the tool result is not RunInBackground", () => {
    expect(buildBackgroundTaskAckCard({ toolName: "WebSearch", output: {} })).toBeNull();
  });

  it("returns null when required fields are missing", () => {
    expect(
      buildBackgroundTaskAckCard({ toolName: "RunInBackground", output: {} }),
    ).toBeNull();
  });

  it("supports a per-bot board href when botId is supplied", () => {
    const card = buildBackgroundTaskAckCard(
      {
        toolName: "RunInBackground",
        output: { taskId: "t1", title: "x", status: "todo", goalMode: false },
      },
      { botId: "bot-7" },
    );
    expect(card?.boardHref).toBe("/dashboard/bot-7/work-queue");
  });

  it("surfaces goal-mode tasks distinctly", () => {
    const card = buildBackgroundTaskAckCard({
      toolName: "RunInBackground",
      output: { taskId: "t1", title: "x", status: "todo", goalMode: true },
    });
    expect(card?.goalMode).toBe(true);
  });
});

describe("parseBackgroundTaskAckFromMessage", () => {
  it("finds the first matching tool call in a message's tool_results array", () => {
    const message = {
      role: "assistant",
      content: "ok",
      tool_results: [
        { toolName: "WebSearch", output: {} },
        {
          toolName: "RunInBackground",
          output: { taskId: "t99", title: "do x", status: "todo", goalMode: false },
        },
      ],
    };
    const card = parseBackgroundTaskAckFromMessage(message);
    expect(card?.taskId).toBe("t99");
  });

  it("returns null when there are no tool results", () => {
    expect(parseBackgroundTaskAckFromMessage({ role: "assistant" })).toBeNull();
  });

  it("returns null when no tool result is a RunInBackground", () => {
    expect(
      parseBackgroundTaskAckFromMessage({
        role: "assistant",
        tool_results: [{ toolName: "WebSearch", output: {} }],
      }),
    ).toBeNull();
  });
});
