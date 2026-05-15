export const CONSULTATION_ASR_CENTS_PER_MINUTE = 3;

export function roundDurationToBillableMinutes(durationSeconds: number): number {
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) return 0;
  return Math.ceil(durationSeconds / 60);
}

export function estimateConsultationCreditsCents(
  durationSeconds: number,
  centsPerMinute = CONSULTATION_ASR_CENTS_PER_MINUTE,
): number {
  return roundDurationToBillableMinutes(durationSeconds) * centsPerMinute;
}
