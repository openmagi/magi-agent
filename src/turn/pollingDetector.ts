const STATUS_CHECK_TOOLS = new Set(["TaskGet"]);
const RUNNING_STATUSES = new Set(["running", "pending"]);

export interface PollingDetectorResult {
  isPolling: boolean;
  allStillRunning: boolean;
}

export function detectPollingIteration(
  toolNames: readonly string[],
  dispatchedResults: ReadonlyArray<{ content: string | unknown; isError: boolean }>,
): PollingDetectorResult {
  if (toolNames.length === 0) {
    return { isPolling: false, allStillRunning: false };
  }
  const allStatusCheck = toolNames.every((name) => STATUS_CHECK_TOOLS.has(name));
  if (!allStatusCheck) {
    return { isPolling: false, allStillRunning: false };
  }

  const allStillRunning = dispatchedResults.every((d) => {
    if (d.isError) return false;
    const raw = typeof d.content === "string" ? d.content : "";
    try {
      const parsed = JSON.parse(raw) as { status?: string };
      return typeof parsed.status === "string" && RUNNING_STATUSES.has(parsed.status);
    } catch {
      return false;
    }
  });

  return { isPolling: true, allStillRunning };
}
