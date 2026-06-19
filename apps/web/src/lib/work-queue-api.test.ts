import { describe, it, expect } from "vitest";
import { groupTasksByStatus, STATUS_COLUMNS, type WorkQueueTask } from "./work-queue-api";

function t(id: string, status: string): WorkQueueTask {
  return {
    id,
    title: id,
    status,
    created_at: 1,
    body: null,
    assignee: null,
    priority: 0,
    goal_mode: false,
    result: null,
    consecutive_failures: 0,
    idempotency_key: null,
    tenant: null,
  };
}

describe("groupTasksByStatus", () => {
  it("buckets tasks into status columns and keeps empty columns", () => {
    const grouped = groupTasksByStatus([t("a", "ready"), t("b", "ready"), t("c", "completed")]);
    expect(grouped.ready.map((x) => x.id)).toEqual(["a", "b"]);
    expect(grouped.completed.map((x) => x.id)).toEqual(["c"]);
    expect(grouped.running).toEqual([]); // known column present even when empty
    for (const col of STATUS_COLUMNS) expect(Array.isArray(grouped[col])).toBe(true);
  });
});
