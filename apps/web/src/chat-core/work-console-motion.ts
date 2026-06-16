export const WORK_CONSOLE_MOTION_TICK_MS = 1_000;
export const WORK_CONSOLE_ROW_STAGGER_MS = 60;
export const WORK_CONSOLE_ROW_STAGGER_MAX_MS = 240;

export function smoothedHeartbeatElapsedMs(
  baseElapsedMs: number | null | undefined,
  observedAtMs: number,
  nowMs: number,
): number | null {
  if (
    typeof baseElapsedMs !== "number" ||
    !Number.isFinite(baseElapsedMs) ||
    baseElapsedMs < 1_000
  ) {
    return null;
  }
  if (!Number.isFinite(observedAtMs) || !Number.isFinite(nowMs)) {
    return Math.max(1_000, Math.floor(baseElapsedMs));
  }
  const localDeltaMs = Math.max(0, nowMs - observedAtMs);
  return Math.floor(baseElapsedMs) + Math.floor(localDeltaMs / 1_000) * 1_000;
}

export function workConsoleRowDelayMs(index: number): number {
  if (!Number.isFinite(index) || index <= 0) return 0;
  return Math.min(
    WORK_CONSOLE_ROW_STAGGER_MAX_MS,
    Math.floor(index) * WORK_CONSOLE_ROW_STAGGER_MS,
  );
}
