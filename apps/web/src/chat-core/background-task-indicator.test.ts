import { describe, expect, it } from "vitest";
import {
  buildBackgroundTaskIndicator,
  type BackgroundTaskInput,
} from "./background-task-indicator";

interface TestTask extends BackgroundTaskInput {
  id?: string;
}

function task(overrides: Partial<TestTask>): TestTask {
  return {
    id: overrides.id ?? "t",
    status: overrides.status ?? "ready",
  };
}

describe("buildBackgroundTaskIndicator", () => {
  it("returns null when there are zero active tasks", () => {
    expect(buildBackgroundTaskIndicator([])).toBeNull();
    expect(buildBackgroundTaskIndicator([task({ status: "completed" })])).toBeNull();
  });

  it("counts running tasks separately from queued ones", () => {
    const ind = buildBackgroundTaskIndicator([
      task({ id: "a", status: "running" }),
      task({ id: "b", status: "running" }),
      task({ id: "c", status: "ready" }),
      task({ id: "d", status: "todo" }),
      task({ id: "e", status: "triage" }),
    ]);
    expect(ind).not.toBeNull();
    expect(ind?.running).toBe(2);
    expect(ind?.queued).toBe(3);
    expect(ind?.active).toBe(5);
  });

  it("excludes terminal statuses from active", () => {
    const ind = buildBackgroundTaskIndicator([
      task({ id: "a", status: "running" }),
      task({ id: "b", status: "completed" }),
      task({ id: "c", status: "failed" }),
      task({ id: "d", status: "blocked" }),
      task({ id: "e", status: "archived" }),
    ]);
    expect(ind?.active).toBe(1);
    expect(ind?.running).toBe(1);
  });

  it("renders a compact label tuned to whether anything is running", () => {
    const onlyQueued = buildBackgroundTaskIndicator([task({ status: "ready" })]);
    expect(onlyQueued?.label).toBe("1 queued");

    const oneRunning = buildBackgroundTaskIndicator([task({ status: "running" })]);
    expect(oneRunning?.label).toBe("1 running");

    const mix = buildBackgroundTaskIndicator([
      task({ id: "a", status: "running" }),
      task({ id: "b", status: "running" }),
      task({ id: "c", status: "ready" }),
    ]);
    expect(mix?.label).toBe("2 running · 1 queued");
  });

  it("threads a per-bot board href when botId is supplied", () => {
    const ind = buildBackgroundTaskIndicator([task({ status: "running" })], { botId: "bot-7" });
    expect(ind?.boardHref).toBe("/dashboard/bot-7/work-queue");
  });

  it("falls back to the flat board href without a botId", () => {
    const ind = buildBackgroundTaskIndicator([task({ status: "running" })]);
    expect(ind?.boardHref).toBe("/dashboard/work-queue");
  });
});
