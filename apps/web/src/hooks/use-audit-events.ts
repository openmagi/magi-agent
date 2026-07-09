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

/**
 * Verify-before-replying process data, present only on rows with
 * `sourceType="verify"` (kind = "turn" | "finding" | "pass").
 * Absent for every non-verify verdict row.
 */
export interface AuditVerdictVerify {
  kind: "turn" | "finding" | "pass";
  // kind === "turn"
  verdict?: "verified_clean" | "revised" | "shipped_acknowledged" | "nudge_ignored";
  passes?: number;
  loopBackToolCalls?: number;
  shipMarkerUsed?: boolean;
  highTotal?: number;
  highResolved?: number;
  highAcknowledged?: number;
  highIgnored?: number;
  advisoryTotal?: number;
  advisoryIgnored?: number;
  corpusRecordCount?: number;
  findingsOmitted?: number;
  context?: string;
  // kind === "finding"
  findingId?: string;
  confidence?: "high" | "advisory";
  claimClass?: string;
  resolution?: "resolved" | "acknowledged_shipped" | "ignored";
  claimText?: string;
  expected?: string;
  observed?: string;
  suggestedAction?: string;
}

export interface AuditVerdict {
  id: string | null;
  kind: string;
  status: string;
  displayLabel: string;
  severity: AuditSeverity;
  subject: string | null;
  reasonCodes: string[];
  /**
   * Glanceable affordance chips for the source-citation gate verdict (repaired /
   * induced search / fail-open). Empty for every non-citation verdict.
   */
  affordances: string[];
  summary: string;
  evidenceRefs: string[];
  /**
   * Verify-before-replying process data. Present only on verify rows;
   * undefined for every non-verify verdict row.
   */
  verify?: AuditVerdictVerify;
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

const VERIFY_KINDS: ReadonlySet<string> = new Set(["turn", "finding", "pass"]);

const VERIFY_VERDICTS: ReadonlySet<string> = new Set([
  "verified_clean",
  "revised",
  "shipped_acknowledged",
  "nudge_ignored",
]);

const VERIFY_CONFIDENCES: ReadonlySet<string> = new Set(["high", "advisory"]);

const VERIFY_RESOLUTIONS: ReadonlySet<string> = new Set([
  "resolved",
  "acknowledged_shipped",
  "ignored",
]);

function asFiniteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function asBoolean(value: unknown): boolean | undefined {
  return value === true || value === false ? value : undefined;
}

function asStringEnum<T extends string>(
  value: unknown,
  allowed: ReadonlySet<string>,
): T | undefined {
  return typeof value === "string" && allowed.has(value)
    ? (value as T)
    : undefined;
}

function asOptionalString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

/**
 * Defensive normalizer for the verify process blob.
 * Returns undefined unless kind is exactly "turn", "finding", or "pass".
 * Validates enum fields against allowlists; invalid values are dropped.
 */
function normalizeVerify(value: unknown): AuditVerdictVerify | undefined {
  if (!isRecord(value)) return undefined;
  const kind = asStringEnum<"turn" | "finding" | "pass">(
    value.kind,
    VERIFY_KINDS,
  );
  if (!kind) return undefined;

  const result: AuditVerdictVerify = { kind };

  if (kind === "turn") {
    const verdict = asStringEnum<
      "verified_clean" | "revised" | "shipped_acknowledged" | "nudge_ignored"
    >(value.verdict, VERIFY_VERDICTS);
    if (verdict !== undefined) result.verdict = verdict;

    const passes = asFiniteNumber(value.passes);
    if (passes !== undefined) result.passes = passes;

    const loopBackToolCalls = asFiniteNumber(value.loopBackToolCalls);
    if (loopBackToolCalls !== undefined) result.loopBackToolCalls = loopBackToolCalls;

    const shipMarkerUsed = asBoolean(value.shipMarkerUsed);
    if (shipMarkerUsed !== undefined) result.shipMarkerUsed = shipMarkerUsed;

    const highTotal = asFiniteNumber(value.highTotal);
    if (highTotal !== undefined) result.highTotal = highTotal;

    const highResolved = asFiniteNumber(value.highResolved);
    if (highResolved !== undefined) result.highResolved = highResolved;

    const highAcknowledged = asFiniteNumber(value.highAcknowledged);
    if (highAcknowledged !== undefined) result.highAcknowledged = highAcknowledged;

    const highIgnored = asFiniteNumber(value.highIgnored);
    if (highIgnored !== undefined) result.highIgnored = highIgnored;

    const advisoryTotal = asFiniteNumber(value.advisoryTotal);
    if (advisoryTotal !== undefined) result.advisoryTotal = advisoryTotal;

    const advisoryIgnored = asFiniteNumber(value.advisoryIgnored);
    if (advisoryIgnored !== undefined) result.advisoryIgnored = advisoryIgnored;

    const corpusRecordCount = asFiniteNumber(value.corpusRecordCount);
    if (corpusRecordCount !== undefined) result.corpusRecordCount = corpusRecordCount;

    const findingsOmitted = asFiniteNumber(value.findingsOmitted);
    if (findingsOmitted !== undefined) result.findingsOmitted = findingsOmitted;

    const context = asOptionalString(value.context);
    if (context !== undefined) result.context = context;
  }

  if (kind === "finding") {
    const findingId = asOptionalString(value.findingId);
    if (findingId !== undefined) result.findingId = findingId;

    const confidence = asStringEnum<"high" | "advisory">(
      value.confidence,
      VERIFY_CONFIDENCES,
    );
    if (confidence !== undefined) result.confidence = confidence;

    const claimClass = asOptionalString(value.claimClass);
    if (claimClass !== undefined) result.claimClass = claimClass;

    const resolution = asStringEnum<
      "resolved" | "acknowledged_shipped" | "ignored"
    >(value.resolution, VERIFY_RESOLUTIONS);
    if (resolution !== undefined) result.resolution = resolution;

    const claimText = asOptionalString(value.claimText);
    if (claimText !== undefined) result.claimText = claimText;

    const expected = asOptionalString(value.expected);
    if (expected !== undefined) result.expected = expected;

    const observed = asOptionalString(value.observed);
    if (observed !== undefined) result.observed = observed;

    const suggestedAction = asOptionalString(value.suggestedAction);
    if (suggestedAction !== undefined) result.suggestedAction = suggestedAction;
  }

  return result;
}

function normalizeVerdict(value: unknown): AuditVerdict {
  const r = isRecord(value) ? value : {};
  const severity = asString(r.severity);
  const verify = normalizeVerify(r.verify);
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
    affordances: asStringArray(r.affordances),
    summary: asString(r.summary),
    evidenceRefs: asStringArray(r.evidenceRefs),
    ...(verify !== undefined ? { verify } : {}),
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
