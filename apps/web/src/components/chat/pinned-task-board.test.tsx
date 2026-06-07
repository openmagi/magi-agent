import React from "react";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PinnedTaskBoardDock, shouldPinTaskBoard } from "./pinned-task-board";
import type { TaskBoardSnapshot } from "@/lib/chat/types";

function snapshot(statuses: Array<"pending" | "in_progress" | "completed" | "cancelled">): TaskBoardSnapshot {
  return {
    receivedAt: 1,
    tasks: statuses.map((status, index) => ({
      id: `task-${index}`,
      title: `Task ${index + 1}`,
      description: `Description ${index + 1}`,
      status,
    })),
  };
}

describe("PinnedTaskBoardDock", () => {
  it("pins the task board while any task is still open", () => {
    const board = snapshot(["completed", "in_progress", "pending"]);

    expect(shouldPinTaskBoard(board)).toBe(true);

    const html = renderToStaticMarkup(<PinnedTaskBoardDock snapshot={board} />);
    expect(html).toContain("aria-label=\"Current task list\"");
    expect(html).toContain("Task 2");
    expect(html).toContain("Task 3");
  });

  it("hides once every task is completed or cancelled", () => {
    const board = snapshot(["completed", "cancelled"]);

    expect(shouldPinTaskBoard(board)).toBe(false);
    expect(renderToStaticMarkup(<PinnedTaskBoardDock snapshot={board} />)).toBe("");
  });
});
