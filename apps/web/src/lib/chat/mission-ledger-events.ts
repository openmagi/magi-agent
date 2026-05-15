export const OPEN_MISSION_LEDGER_EVENT = "clawy:open-mission-ledger";

export interface OpenMissionLedgerEventDetail {
  missionId: string;
}

export function readOpenMissionLedgerEvent(event: Event): OpenMissionLedgerEventDetail | null {
  if (!(event instanceof CustomEvent)) return null;
  const detail = event.detail;
  if (!detail || typeof detail !== "object") return null;
  const missionId = (detail as { missionId?: unknown }).missionId;
  if (typeof missionId !== "string") return null;
  const trimmed = missionId.trim();
  return trimmed ? { missionId: trimmed } : null;
}

export function dispatchOpenMissionLedgerEvent(missionId: string): void {
  if (typeof window === "undefined") return;
  const trimmed = missionId.trim();
  if (!trimmed) return;
  window.dispatchEvent(new CustomEvent(OPEN_MISSION_LEDGER_EVENT, {
    detail: { missionId: trimmed } satisfies OpenMissionLedgerEventDetail,
  }));
}
