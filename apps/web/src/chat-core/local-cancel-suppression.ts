export const LOCAL_CANCEL_SUPPRESSION_MS = 2 * 60_000;

export function markLocalCancelSuppressed(
  suppressions: Record<string, number>,
  channel: string,
  now = Date.now(),
  windowMs = LOCAL_CANCEL_SUPPRESSION_MS,
): void {
  suppressions[channel] = now + windowMs;
}

export function clearLocalCancelSuppression(
  suppressions: Record<string, number>,
  channel: string,
): void {
  delete suppressions[channel];
}

export function isLocalCancelSuppressed(
  suppressions: Record<string, number>,
  channel: string,
  now = Date.now(),
): boolean {
  const until = suppressions[channel];
  if (!until) return false;
  if (now < until) return true;
  delete suppressions[channel];
  return false;
}
