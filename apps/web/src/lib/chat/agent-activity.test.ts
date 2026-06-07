import { describe, expect, it } from "vitest";
import {
  deriveAgentActivityItems,
  formatActivityDuration,
  getAgentActivitySummary,
} from "./agent-activity";
import type { TaskBoardSnapshot, ToolActivity } from "./types";

function activity(
  label: string,
  status: ToolActivity["status"] = "done",
  durationMs = 1200,
): ToolActivity {
  return {
    id: `${label}-${status}`,
    label,
    status,
    startedAt: 1_000,
    durationMs,
    inputPreview: status === "running" ? "input" : undefined,
    outputPreview: status === "done" ? "output" : undefined,
  };
}

function board(): TaskBoardSnapshot {
  return {
    receivedAt: 10_000,
    tasks: [
      { id: "t1", title: "Read files", description: "Inspect chat UI", status: "completed" },
      { id: "t2", title: "Build timeline", description: "Render activity rows", status: "in_progress" },
      { id: "t3", title: "Verify", description: "Run checks", status: "pending" },
    ],
  };
}

describe("deriveAgentActivityItems", () => {
  it("shows file processing and live thinking rows first", () => {
    const rows = deriveAgentActivityItems({
      live: true,
      fileProcessing: true,
      startedAt: 1_000,
      now: 13_400,
    });

    expect(rows.map((r) => r.label)).toEqual([
      "Processing attachments",
      "12s 동안 작업",
    ]);
    expect(rows.every((r) => r.status === "running")).toBe(true);
  });

  it("shows thinking content as preview when phase is active", () => {
    const rows = deriveAgentActivityItems({
      live: true,
      startedAt: 1_000,
      now: 16_000,
      turnPhase: "executing",
      thinkingContent: "reasoning about the user request",
    });

    expect(rows[0]).toMatchObject({
      id: "phase-executing",
      label: "Running current step",
      detail: "15s",
      status: "running",
      inputPreview: "reasoning about the user request",
    });
    expect(rows[0]?.outputPreview).toBeUndefined();
  });

  it("surfaces queued follow-ups while a turn is still live", () => {
    const rows = deriveAgentActivityItems({
      live: true,
      turnPhase: "planning",
      pendingInjectionCount: 2,
    });

    expect(rows.map((r) => r.label)).toContain("2 follow-ups queued");
  });

  it("uses heartbeat as a fallback progress row", () => {
    const rows = deriveAgentActivityItems({
      live: true,
      heartbeatElapsedMs: 42_000,
    });

    expect(rows[0]).toMatchObject({
      id: "heartbeat",
      label: "Still working on current step",
      detail: "42s",
      status: "running",
    });
  });

  it("summarizes a live task board", () => {
    const rows = deriveAgentActivityItems({
      live: true,
      taskBoard: board(),
    });

    expect(rows[0]).toMatchObject({
      id: "task-board",
      label: "Working on 1 task",
      detail: "1/3 complete",
      status: "running",
    });
  });

  it("groups completed command and read activities", () => {
    const rows = deriveAgentActivityItems({
      activities: [
        activity("exec_command npm test"),
        activity("exec_command git status"),
        activity("rg chat"),
        activity("fetch_file chat-view-client.tsx"),
      ],
    });

    expect(rows.map((r) => r.label)).toEqual([
      "Ran 2 commands",
      "Read 2 files",
    ]);
    expect(rows.every((r) => r.status === "done")).toBe(true);
  });

  it("turns completed thinking into a collapsed thought activity", () => {
    const rows = deriveAgentActivityItems({
      thinkingDuration: 53,
      thinkingContent: "Private reasoning\nFinal strategy notes",
    });

    expect(rows).toEqual([
      expect.objectContaining({
        id: "thought",
        label: "53s 동안 작업",
        status: "done",
      }),
    ]);
    expect(rows[0]?.outputPreview).toBeUndefined();
    expect(getAgentActivitySummary(rows)).toBe("53s 동안 작업");
  });

  it("keeps running, error, and denied activities explicit", () => {
    const rows = deriveAgentActivityItems({
      live: true,
      activities: [
        activity("exec_command npm run build", "running"),
        activity("knowledge-search", "error"),
        activity("shell rm -rf", "denied"),
      ],
    });

    expect(rows.map((r) => [r.label, r.status])).toEqual([
      ["Running exec_command npm run build", "running"],
      ["knowledge-search failed", "error"],
      ["shell rm -rf denied", "denied"],
    ]);
  });

  it("propagates input/output previews for running and error tool rows", () => {
    const rows = deriveAgentActivityItems({
      live: true,
      activities: [
        {
          id: "tool-1",
          label: "exec_command",
          status: "running",
          startedAt: 1_000,
          inputPreview: "raw command input",
          outputPreview: "raw command output",
        },
        {
          id: "tool-2",
          label: "knowledge-search",
          status: "error",
          startedAt: 1_000,
          inputPreview: "raw query",
          outputPreview: "raw result",
        },
      ],
    });

    expect(rows.map((r) => [r.id, r.label, r.status])).toEqual([
      ["tool-1", "Running exec_command", "running"],
      ["tool-2", "knowledge-search failed", "error"],
    ]);
    expect(rows[0]?.inputPreview).toBe("raw command input");
    expect(rows[0]?.outputPreview).toBeUndefined();
    expect(rows[1]?.inputPreview).toBeUndefined();
    expect(rows[1]?.outputPreview).toBe("raw result");
  });

  it("does not render archived run metadata as still in progress", () => {
    const rows = deriveAgentActivityItems({
      live: false,
      activities: [
        activity("exec_command npm run build", "running"),
        activity("knowledge-search", "done"),
      ],
      taskBoard: board(),
    });

    expect(rows.every((row) => row.status !== "running")).toBe(true);
    expect(getAgentActivitySummary(rows)).toBe("Ran 3 actions");
  });

  it("counts all visible rows in the live collapsed summary", () => {
    const rows = deriveAgentActivityItems({
      live: true,
      turnPhase: "executing",
      activities: [
        activity("DocumentWrite", "error", 0),
        activity("Bash", "denied", 8),
        activity("exec_command npm test"),
        activity("fetch_file run-inspector-dock.tsx"),
      ],
    });

    expect(rows.map((r) => [r.label, r.status])).toEqual([
      ["Running current step", "running"],
      ["DocumentWrite failed", "error"],
      ["Bash denied", "denied"],
      ["Ran 1 command", "done"],
      ["Read 1 file", "done"],
    ]);
    expect(getAgentActivitySummary(rows)).toBe("5 actions in progress");
  });

  it("builds completed summaries from rows", () => {
    const summary = getAgentActivitySummary(
      deriveAgentActivityItems({
        activities: [
          activity("exec_command npm test"),
          activity("rg chat"),
          activity("knowledge-search"),
        ],
      }),
    );

    expect(summary).toBe("Ran 3 actions");
  });
});

describe("formatActivityDuration", () => {
  it("formats missing, millisecond, and second durations", () => {
    expect(formatActivityDuration()).toBeNull();
    expect(formatActivityDuration(999)).toBe("999ms");
    expect(formatActivityDuration(1250)).toBe("1.3s");
  });
});
