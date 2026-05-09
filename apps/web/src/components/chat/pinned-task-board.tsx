"use client";

import { TaskBoard } from "./task-board";
import type { TaskBoardSnapshot } from "@/lib/chat/types";

interface PinnedTaskBoardDockProps {
  snapshot?: TaskBoardSnapshot | null;
}

export function shouldPinTaskBoard(snapshot?: TaskBoardSnapshot | null): boolean {
  return !!snapshot?.tasks.some(
    (task) => task.status === "pending" || task.status === "in_progress",
  );
}

export function PinnedTaskBoardDock({ snapshot }: PinnedTaskBoardDockProps) {
  if (!shouldPinTaskBoard(snapshot)) return null;

  return (
    <div className="px-4 md:px-8 lg:px-12 pb-2" aria-label="Current task list">
      <div className="mx-auto max-w-5xl">
        <TaskBoard snapshot={snapshot!} />
      </div>
    </div>
  );
}
