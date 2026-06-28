"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { usePoll } from "@/hooks/use-poll";
import { loadLocalBootstrap } from "@/lib/local-auth";

/**
 * Audit panel data hook (Phase 1, post-hoc poll).
 *
 * Reads the per-session policy-enforcement verdict surface added by the
 * backend (PR1): `GET /api/observability/v1/sessions/{sessionId}/audit`. The
 * endpoint is flag-gated server-side (404 `{"error":"feature_disabled"}` when
 * `MAGI_CHAT_AUDIT_PANEL_ENABLED` is off), which this hook treats as
 * "no data, not an error". Auth + base URL are handled by `agentFetch`
 * (loopback gateway token), mirroring `use-bot-status.ts`.
 */

export type AuditSeverity = "pass" | "deny" | "review" | "info";

export type AuditCredibility = "credible" | "unverified" | "contradicted";

export interface AuditVerdict {
  id: string | null;
  kind: string;
  status: string;
  displayLabel: string;
  severity: AuditSeverity;
  subject: string | null;
  reasonCodes: string[];
  summary: string;
  evidenceRefs: string[];
}

export interface AuditRunGroup {
  runId: string | null;
  startedAt: number | null;
  policyCount: number;
  verdicts: AuditVerdict[];
}

export interface AuditSource {
  label: string;
  uri: string;
  verified: boolean;
  credibility: AuditCredibility;
}

export interface AuditData {
  sessionId: string;
  runs: AuditRunGroup[];
  sources: AuditSource[];
}

export interface UseAuditEventsResult {
  data: AuditData | null;
  loading: boolean;
  error: string | null;
}

const POLL_INTERVAL_MS = 5_000;

function buildAuditUrl(sessionId: string): string {
  return `/api/observability/v1/sessions/${encodeURIComponent(sessionId)}/audit`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

const AUDIT_SEVERITIES: ReadonlySet<string> = new Set([
  "pass",
  "deny",
  "review",
  "info",
]);

const AUDIT_CREDIBILITIES: ReadonlySet<string> = new Set([
  "credible",
  "unverified",
  "contradicted",
]);

function normalizeVerdict(value: unknown): AuditVerdict {
  const r = isRecord(value) ? value : {};
  const severity = asString(r.severity);
  return {
    id: typeof r.id === "string" ? r.id : null,
    kind: asString(r.kind),
    status: asString(r.status, "info"),
    displayLabel: asString(r.displayLabel, asString(r.status, "Verdict")),
    severity: AUDIT_SEVERITIES.has(severity)
      ? (severity as AuditSeverity)
      : "info",
    subject: typeof r.subject === "string" ? r.subject : null,
    reasonCodes: asStringArray(r.reasonCodes),
    summary: asString(r.summary),
    evidenceRefs: asStringArray(r.evidenceRefs),
  };
}

function normalizeRun(value: unknown): AuditRunGroup {
  const r = isRecord(value) ? value : {};
  return {
    runId: typeof r.runId === "string" ? r.runId : null,
    startedAt:
      typeof r.startedAt === "number" && Number.isFinite(r.startedAt)
        ? r.startedAt
        : null,
    policyCount: Number(r.policyCount) || 0,
    verdicts: Array.isArray(r.verdicts) ? r.verdicts.map(normalizeVerdict) : [],
  };
}

function normalizeSource(value: unknown): AuditSource {
  const r = isRecord(value) ? value : {};
  const credibility = asString(r.credibility);
  return {
    label: asString(r.label),
    uri: asString(r.uri),
    verified: r.verified === true,
    credibility: AUDIT_CREDIBILITIES.has(credibility)
      ? (credibility as AuditCredibility)
      : "unverified",
  };
}

/**
 * `botId` is part of the public signature for symmetry with the other panel
 * hooks; the audit read is keyed purely by the backend observability
 * `sessionId`, so it is intentionally unused here.
 */
export function useAuditEvents(
  botId: string,
  sessionId?: string | null,
): UseAuditEventsResult {
  void botId;
  const authFetch = useAuthFetch();
  const [data, setData] = useState<AuditData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadedOnceRef = useRef(false);
  // Latest session this hook has been asked to load. Captured at call start and
  // re-checked before any setState so an in-flight fetch for a now-stale session
  // (channel switch) can never clobber the current session's data.
  const latestSessionRef = useRef<string | null | undefined>(sessionId);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const enabled = Boolean(sessionId);

  const load = useCallback(async () => {
    if (!sessionId) return;
    const requestedSession = sessionId;
    // Bail if the request is already stale or the component unmounted.
    const isCurrent = () =>
      mountedRef.current && latestSessionRef.current === requestedSession;
    if (!loadedOnceRef.current) setLoading(true);
    try {
      const res = await authFetch(buildAuditUrl(requestedSession));
      if (!isCurrent()) return;
      if (res.status === 404) {
        // Feature disabled server-side — present as empty, never an error toast.
        setData(null);
        setError(null);
        return;
      }
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const body: unknown = await res.json();
      if (!isCurrent()) return;
      const record = isRecord(body) ? body : {};
      setData({
        sessionId:
          typeof record.sessionId === "string"
            ? record.sessionId
            : requestedSession,
        runs: Array.isArray(record.runs) ? record.runs.map(normalizeRun) : [],
        sources: Array.isArray(record.sources)
          ? record.sources.map(normalizeSource)
          : [],
      });
      setError(null);
    } catch (err) {
      if (!isCurrent()) return;
      setError(err instanceof Error ? err.message : "Failed to load audit log");
    } finally {
      if (isCurrent()) {
        loadedOnceRef.current = true;
        setLoading(false);
      }
    }
  }, [authFetch, sessionId]);

  // Reset and reload whenever the session changes (idle when falsy).
  useEffect(() => {
    latestSessionRef.current = sessionId;
    loadedOnceRef.current = false;
    setData(null);
    setError(null);
    setLoading(false);
    if (sessionId) {
      void load();
    }
  }, [sessionId, load]);

  usePoll(load, POLL_INTERVAL_MS, enabled);

  return { data, loading, error };
}

/**
 * Reads `features.auditPanel` from the already-cached local bootstrap. Returns
 * false until the bootstrap resolves and whenever the flag/feature is absent,
 * so the Audit tab stays hidden by default (default-OFF contract).
 */
export function useAuditEnabled(): boolean {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    let active = true;
    void loadLocalBootstrap().then((bootstrap) => {
      if (active) setEnabled(bootstrap?.features?.auditPanel === true);
    });
    return () => {
      active = false;
    };
  }, []);

  return enabled;
}
