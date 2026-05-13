import { describe, it, expect } from "vitest";
import { detectPollingIteration } from "./pollingDetector.js";

describe("pollingDetector", () => {
  it("returns not-polling for empty tool list", () => {
    const result = detectPollingIteration([], []);
    expect(result).toEqual({ isPolling: false, allStillRunning: false });
  });

  it("returns not-polling when non-status-check tools are present", () => {
    const result = detectPollingIteration(
      ["TaskGet", "Read"],
      [
        { content: '{"status":"running"}', isError: false },
        { content: "file data", isError: false },
      ],
    );
    expect(result.isPolling).toBe(false);
  });

  it("detects polling when all tools are TaskGet", () => {
    const result = detectPollingIteration(
      ["TaskGet", "TaskGet"],
      [
        { content: '{"status":"running"}', isError: false },
        { content: '{"status":"pending"}', isError: false },
      ],
    );
    expect(result).toEqual({ isPolling: true, allStillRunning: true });
  });

  it("detects polling but not-all-running when a task completed", () => {
    const result = detectPollingIteration(
      ["TaskGet"],
      [{ content: '{"status":"completed"}', isError: false }],
    );
    expect(result).toEqual({ isPolling: true, allStillRunning: false });
  });

  it("detects polling but not-all-running on error results", () => {
    const result = detectPollingIteration(
      ["TaskGet"],
      [{ content: '{"status":"running"}', isError: true }],
    );
    expect(result).toEqual({ isPolling: true, allStillRunning: false });
  });

  it("handles non-JSON content gracefully", () => {
    const result = detectPollingIteration(
      ["TaskGet"],
      [{ content: "not json", isError: false }],
    );
    expect(result).toEqual({ isPolling: true, allStillRunning: false });
  });

  it("handles non-string content", () => {
    const result = detectPollingIteration(
      ["TaskGet"],
      [{ content: 42 as unknown as string, isError: false }],
    );
    expect(result).toEqual({ isPolling: true, allStillRunning: false });
  });
});
