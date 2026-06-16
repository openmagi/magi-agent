export const OPEN_MISSION_LEDGER_EVENT = "clawy:open-mission-ledger";

export interface OpenMissionLedgerEventDetail {
  missionId: string;
}

const PUBLIC_MISSION_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,180}$/;
const SECRET_MISSION_ID_RE =
  /(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|sk-(?:live|test)?[-_A-Za-z0-9]{8,})/i;
const PRIVATE_MISSION_ID_RE =
  /(?:^|[:_\-.])(session|secret|token|credential|password)(?:$|[:_\-.])|(?:^|[/:])(?:Users|home|workspace|private|var)(?:[/:]|$)|(?:^|[_:\-.])raw(?:[_:\-. -]?(?:prompt|tool|result|output|log|args|transcript))/i;

function sanitizeMissionId(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (!PUBLIC_MISSION_ID_RE.test(trimmed)) return null;
  if (SECRET_MISSION_ID_RE.test(trimmed) || PRIVATE_MISSION_ID_RE.test(trimmed)) return null;
  return trimmed;
}

export function readOpenMissionLedgerEvent(event: Event): OpenMissionLedgerEventDetail | null {
  if (!(event instanceof CustomEvent)) return null;
  const detail = event.detail;
  if (!detail || typeof detail !== "object") return null;
  const missionId = (detail as { missionId?: unknown }).missionId;
  if (typeof missionId !== "string") return null;
  const safeMissionId = sanitizeMissionId(missionId);
  return safeMissionId ? { missionId: safeMissionId } : null;
}

export function dispatchOpenMissionLedgerEvent(missionId: string): void {
  if (typeof window === "undefined") return;
  const safeMissionId = sanitizeMissionId(missionId);
  if (!safeMissionId) return;
  window.dispatchEvent(new CustomEvent(OPEN_MISSION_LEDGER_EVENT, {
    detail: { missionId: safeMissionId } satisfies OpenMissionLedgerEventDetail,
  }));
}
